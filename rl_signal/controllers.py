"""Rule-based controllers for the controlled mode.

Both decide every DECISION_INTERVAL s, like the DQN, and may pick the 4 green phases in any order;
env.py applies the same signal rules to all of them.

- LongestQueue: the phase whose green lanes hold the most halted vehicles (the rule from the
  IMSE7150 course project).
- MaxPressure (Varaiya, 2013): the phase with the largest sum, over its green links, of vehicles
  on the incoming lane minus vehicles on the outgoing lane. At an isolated junction whose exits
  never back up, the outgoing term is small, so it behaves much like LongestQueue except that it
  counts moving vehicles too. It matters more in a network (stage B).
"""


class LongestQueue:
    name = "longest_queue"

    def reset(self, env):
        self.lanes = [env.lanes_of_phase(k)[0] for k in range(len(env.states))]

    def act(self, env, obs):
        if not env.can_switch():
            return env.phase
        scores = [sum(env.halting[l] for l in lanes) for lanes in self.lanes]
        best = max(scores)
        return env.phase if scores[env.phase] == best else scores.index(best)


class MaxPressure:
    name = "max_pressure"

    def reset(self, env):
        self.pairs = [env.lanes_of_phase(k)[1] for k in range(len(env.states))]
        self.out_lanes = sorted({o for pairs in self.pairs for _, o in pairs})

    def act(self, env, obs):
        if not env.can_switch():
            return env.phase
        out = {l: env.conn.lane.getLastStepVehicleNumber(l) for l in self.out_lanes}
        scores = [sum(env.vehicles[i] - out[o] for i, o in pairs) for pairs in self.pairs]
        best = max(scores)
        return env.phase if scores[env.phase] == best else scores.index(best)
