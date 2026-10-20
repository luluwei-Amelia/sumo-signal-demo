"""The junction environment, shared by all controllers so they follow the same rules and are
scored by the same code.

controlled mode: a controller picks one of the 4 green phases every DECISION_INTERVAL s. Rules
applied here: green at least MIN_GREEN and at most MAX_GREEN (the actuated program's maxDur); a
phase red for MAX_RED with vehicles on its lanes goes next; YELLOW s of yellow at every change.
env.phase is the phase that actually runs.
native mode: SUMO runs its own program (Webster plan or actuated) and the environment only steps.

Observation: halted and total vehicles on each of the 12 incoming lanes (/ LANE_CAP), current
phase (one-hot), time in green (/ MAX_GREEN), time each phase has been red (/ MAX_RED).
Reward: minus the vehicle-seconds of waiting since the last decision, / REWARD_SCALE. Vehicles
that cannot enter because the queue reaches the entry count as waiting; otherwise a controller
could push a queue out of the network. Variant b1b adds a weight on long-red queues (_advance).

Score: delay = timeLoss + departDelay (as in hk_corridor), waiting = halted time + departDelay,
and the longest single-vehicle wait. Vehicles still unfinished at the end are kept, with the
delay so far (in the network) or the time since their planned departure (never entered); both are
lower bounds and the run is marked invalid. Dropping them made a controller that starved two
approaches score better than max-pressure.
"""
import xml.etree.ElementTree as ET

import numpy as np
import traci
import traci.constants as tc
from sumolib import checkBinary

from build_net import GREEN_PHASES, TURNS, green_states, yellow_state
from common import (CLEARANCE, DECISION_INTERVAL, LONG_RED, LONG_RED_WEIGHT, MAX_GREEN, MAX_RED, MIN_GREEN,
                    NET_FILE, NET_FILE_ACTUATED, RUN_DIR, STEP_LENGTH, TLS_ID, YELLOW)
from demand import demand_end
import webster

IN_LANES = [f"{a}_in_{i}" for a in TURNS for i in range(3)]
LANE_CAP = 40.0          # about 300 m / 7.5 m per queued car
REWARD_SCALE = 100.0
N_PHASES = len(GREEN_PHASES)
OBS_SIZE = 2 * len(IN_LANES) + 2 * N_PHASES + 1


class JunctionEnv:
    def __init__(self, routes, seed, label, mode="controlled", plan=None, tripinfo=True):
        """mode: 'controlled', or 'native' with plan 'webster' or 'actuated'."""
        self.routes, self.seed, self.label, self.mode, self.plan = routes, seed, label, mode, plan
        self.tripinfo = RUN_DIR / f"tripinfo_{label}.xml" if tripinfo else None
        self.collision_file = RUN_DIR / f"collisions_{label}.xml"
        self.states = green_states(NET_FILE)
        self.end_time = demand_end() + CLEARANCE
        self.conn = None

    def reset(self):
        self.close()
        RUN_DIR.mkdir(exist_ok=True)
        net = NET_FILE_ACTUATED if self.plan == "actuated" else NET_FILE
        cmd = [checkBinary("sumo"), "-n", str(net), "-r", str(self.routes), "--seed", str(self.seed),
               "--step-length", str(STEP_LENGTH), "--end", str(self.end_time),
               "--time-to-teleport", "-1", "--collision.action", "warn", "--collision.check-junctions", "true",
               "--collision-output", str(self.collision_file), "--waiting-time-memory", "100000",
               "--no-step-log", "true", "--no-warnings", "true"]
        if self.plan == "webster":
            cmd += ["-a", str(webster.TLL_FILE)]
        if self.tripinfo:
            cmd += ["--tripinfo-output", str(self.tripinfo)]
        traci.start(cmd, label=self.label)
        self.conn = traci.getConnection(self.label)
        if self.plan == "webster":
            self.conn.trafficlight.setProgram(TLS_ID, webster.PROGRAM_ID)
        for lane in IN_LANES:
            self.conn.lane.subscribe(lane, [tc.LAST_STEP_VEHICLE_HALTING_NUMBER, tc.LAST_STEP_VEHICLE_NUMBER])
        self.phase, self.elapsed = 0, 0
        self.red_since = [None] + [0.0] * (N_PHASES - 1)   # None while green
        if self.mode == "controlled":
            self.conn.trafficlight.setRedYellowGreenState(TLS_ID, self.states[0])
            self.phase_lanes = [self.lanes_of_phase(k)[0] for k in range(N_PHASES)]
        self._read()
        return self.observation()

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _read(self):
        res = self.conn.lane.getAllSubscriptionResults()
        self.halting = {l: res[l][tc.LAST_STEP_VEHICLE_HALTING_NUMBER] for l in IN_LANES}
        self.vehicles = {l: res[l][tc.LAST_STEP_VEHICLE_NUMBER] for l in IN_LANES}
        self.pending = len(self.conn.simulation.getPendingVehicles())

    def _advance(self, seconds):
        """Step the simulation and return the waiting for the reward: vehicle-seconds, plus
        LONG_RED_WEIGHT x vehicles halted on phases red for LONG_RED or more (b1b only; score()
        does not use it)."""
        waited = 0.0
        for _ in range(int(seconds / STEP_LENGTH)):
            self.conn.simulationStep()
            self._read()
            waited += (sum(self.halting.values()) + self.pending) * STEP_LENGTH
            if LONG_RED_WEIGHT and self.mode == "controlled":
                t = self.time()
                long_red = [k for k in range(N_PHASES)
                            if self.red_since[k] is not None and t - self.red_since[k] >= LONG_RED]
                waited += LONG_RED_WEIGHT * STEP_LENGTH * sum(self.halting[l] for k in long_red
                                                              for l in self.phase_lanes[k])
        return waited

    def time(self):
        return self.conn.simulation.getTime()

    def done(self):
        t = self.time()
        return t >= self.end_time or (t >= demand_end() and self.conn.simulation.getMinExpectedNumber() == 0)

    def can_switch(self):
        return self.elapsed >= MIN_GREEN

    def must_switch(self):
        return self.elapsed >= MAX_GREEN

    def red_time(self, k):
        return 0.0 if self.red_since[k] is None else self.time() - self.red_since[k]

    def overdue(self):
        """The phase that has waited longest beyond MAX_RED with vehicles on its lanes, or None."""
        late = [k for k in range(N_PHASES) if k != self.phase and self.red_time(k) >= MAX_RED
                and any(self.vehicles[l] for l in self.phase_lanes[k])]
        return max(late, key=self.red_time) if late else None

    def step(self, action):
        """Apply a green-phase choice; returns (observation, reward, done)."""
        waited = 0.0
        if self.can_switch():
            late = self.overdue()
            if late is not None:
                action = late
            elif self.must_switch() and action == self.phase:
                action = (self.phase + 1) % N_PHASES
        if action != self.phase and self.can_switch():
            self.conn.trafficlight.setRedYellowGreenState(TLS_ID, yellow_state(self.states[self.phase],
                                                                                 self.states[action]))
            self.red_since[self.phase] = self.time()
            waited += self._advance(YELLOW)
            self.phase, self.elapsed = action, 0
            self.red_since[action] = None
            self.conn.trafficlight.setRedYellowGreenState(TLS_ID, self.states[action])
        waited += self._advance(DECISION_INTERVAL)
        self.elapsed += DECISION_INTERVAL
        return self.observation(), -waited / REWARD_SCALE, self.done()

    def observation(self):
        q = [self.halting[l] / LANE_CAP for l in IN_LANES]
        n = [self.vehicles[l] / LANE_CAP for l in IN_LANES]
        onehot = [1.0 if k == self.phase else 0.0 for k in range(N_PHASES)]
        red = [min(self.red_time(k) / MAX_RED, 1.0) for k in range(N_PHASES)]
        return np.array(q + n + onehot + [min(self.elapsed / MAX_GREEN, 1.0)] + red, dtype=np.float32)

    def lanes_of_phase(self, k):
        """Incoming lanes with a green link in green phase k, and (in_lane, out_lane) pairs."""
        links = self.conn.trafficlight.getControlledLinks(TLS_ID)
        pairs = [(lk[0][0], lk[0][1]) for i, lk in enumerate(links) if lk and self.states[k][i] == "G"]
        return sorted({p[0] for p in pairs}), pairs

    def run_native(self):
        while not self.done():
            self.conn.simulationStep(self.time() + 60)

    def _unfinished_rows(self):
        """(approach, delay, wait) lower bounds for vehicles that have not arrived."""
        t = self.time()
        rows = []
        for v in self.conn.vehicle.getIDList():
            dd = self.conn.vehicle.getDepartDelay(v)
            rows.append((self.conn.vehicle.getRoute(v)[0][0], self.conn.vehicle.getTimeLoss(v) + dd,
                         self.conn.vehicle.getAccumulatedWaitingTime(v) + dd))
        pending = set(self.conn.simulation.getPendingVehicles())
        if pending:
            for _, el in ET.iterparse(self.routes):
                if el.tag == "trip" and el.get("id") in pending:
                    d = t - float(el.get("depart"))
                    rows.append((el.get("from")[0], d, d))
        return rows

    def score(self):
        """Close SUMO and summarise the run. Call after the episode has finished."""
        unfinished = self._unfinished_rows()
        self.close()
        collisions = sum(1 for _, el in ET.iterparse(self.collision_file) if el.tag == "collision")
        rows = []
        for _, el in ET.iterparse(self.tripinfo):
            if el.tag == "tripinfo":
                dd = float(el.get("departDelay"))
                rows.append((el.get("departLane")[0], float(el.get("timeLoss")) + dd,
                             float(el.get("waitingTime")) + dd))
                el.clear()
        rows += unfinished
        approach = np.array([r[0] for r in rows])
        delay = np.array([r[1] for r in rows])
        wait = np.array([r[2] for r in rows])
        out = {"vehicles": len(rows), "unfinished": len(unfinished), "collisions": collisions,
               "mean_delay": float(delay.mean()), "mean_wait": float(wait.mean()),
               "p95_delay": float(np.percentile(delay, 95)), "max_wait": float(wait.max())}
        for a in TURNS:
            out[f"delay_{a}"] = float(delay[approach == a].mean())
        out["valid"] = out["unfinished"] == 0 and out["collisions"] == 0
        return out
