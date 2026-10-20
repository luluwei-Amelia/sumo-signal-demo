"""Double DQN (van Hasselt et al., 2016) for the junction.

Q(obs) gives one value per green phase; the controller takes the largest.

Training, per seed: EPISODES mornings, a new traffic seed each. Epsilon-greedy, 1.0 to 0.05 over
the first 40% of decisions. Only moments when a switch is allowed are decisions; the waiting in
between is added to the previous decision's reward, discounted per 5-s step, and the stored action
is the phase that ran (rules in env.py may override the choice). After WARMUP decisions, each
decision trains on a batch from the replay buffer with Huber loss on

    r + gamma^k * Q_target(s', argmax_a Q(s', a))

Every VALIDATE_EVERY episodes the greedy policy runs on the VALIDATION traffic; the network with
the lowest delay among all-valid rounds is saved. Evaluation seeds are not used here.

Variant a trains and validates on the 22 Sep profile; b1a and b1b train on randomised demand
(demand.random_factors) and also validate on it.

Usage: python dqn.py      all TRAIN_SEEDS in parallel (WORKERS)
       python dqn.py 0    seed 0 only
"""
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import torch
from torch import nn

import demand
from common import MODEL_DIR, QUICK, RESULTS_DIR, TRAIN_SEEDS, VARIANT, WORKERS
from env import N_PHASES, OBS_SIZE, JunctionEnv

EPISODES = int(os.environ.get("EPISODES", 3 if QUICK else 60))
VALIDATE_EVERY = 1 if QUICK else 5
VALIDATION_SEEDS = (200,) if QUICK else (200, 201)          # 22 Sep profile
RANDOM_VALIDATION_SEEDS = (210,) if QUICK else (210, 211)   # randomised demand (b1 only)
TRAIN_SCENARIO = "train" if VARIANT == "a" else "random"
VALIDATION = [("train", s) for s in VALIDATION_SEEDS]
if VARIANT != "a":
    VALIDATION += [("random", s) for s in RANDOM_VALIDATION_SEEDS]
GAMMA = 0.95            # per 5-s decision step; same value as Astra's PPO agents
LR = 5e-4
BATCH = 64
BUFFER = 100_000
WARMUP = 500 if QUICK else 2_000
TARGET_EVERY = 1_000
EPS_START, EPS_END, EPS_FRACTION = 1.0, 0.05, 0.4
DECISIONS_PER_EPISODE = 1_600   # rough count, used only for the epsilon schedule
HIDDEN = 128


class QNet(nn.Module):
    def __init__(self):
        super().__init__()
        self.layers = nn.Sequential(nn.Linear(OBS_SIZE, HIDDEN), nn.ReLU(),
                                    nn.Linear(HIDDEN, HIDDEN), nn.ReLU(),
                                    nn.Linear(HIDDEN, N_PHASES))

    def forward(self, x):
        return self.layers(x)


class Replay:
    def __init__(self, size, rng):
        self.obs = np.zeros((size, OBS_SIZE), np.float32)
        self.next_obs = np.zeros((size, OBS_SIZE), np.float32)
        self.action = np.zeros(size, np.int64)
        self.reward = np.zeros(size, np.float32)
        self.discount = np.zeros(size, np.float32)   # gamma^k, or 0 at the end of an episode
        self.size, self.n, self.i, self.rng = size, 0, 0, rng

    def add(self, o, a, r, d, o2):
        self.obs[self.i], self.action[self.i], self.reward[self.i] = o, a, r
        self.discount[self.i], self.next_obs[self.i] = d, o2
        self.i = (self.i + 1) % self.size
        self.n = min(self.n + 1, self.size)

    def sample(self, k):
        idx = self.rng.integers(0, self.n, k)
        arrays = (self.obs, self.action, self.reward, self.discount, self.next_obs)
        return [torch.from_numpy(x[idx]) for x in arrays]


class DQNController:
    """Greedy policy of a trained network."""
    name = "dqn"

    def __init__(self, net):
        self.net = net

    def reset(self, env):
        pass

    def act(self, env, obs):
        if not env.can_switch():
            return env.phase
        with torch.no_grad():
            return int(self.net(torch.from_numpy(obs)).argmax())


def run_episode(env, choose):
    """Run one episode, calling choose(obs) at each decision.

    Yields (obs, phase that ran, reward, discount, next_obs) per decision.
    """
    obs = env.reset()
    pending = None   # [obs, action, accumulated reward, gamma^k]
    done = False
    while not done:
        if env.can_switch() or env.must_switch():
            action = choose(obs)
            obs2, r, done = env.step(action)
            if pending is not None:
                yield pending[0], pending[1], pending[2], pending[3], obs
            pending = [obs, env.phase, r, GAMMA]
        else:
            obs2, r, done = env.step(env.phase)
            if pending is not None:
                pending[2] += pending[3] * r
                pending[3] *= GAMMA
        obs = obs2
    if pending is not None:
        yield pending[0], pending[1], pending[2], 0.0, obs


def evaluate(net, scenario, seeds, label):
    """Scores (env.score) of the greedy policy, one per traffic seed."""
    ctl = DQNController(net)
    scores = []
    for s in seeds:
        env = JunctionEnv(demand.write_routes(scenario, s), s, f"{label}_{scenario}_{s}")
        for _ in run_episode(env, lambda o: ctl.act(env, o)):
            pass
        scores.append(env.score())
    return scores


def train(seed):
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    rng = np.random.default_rng(seed)
    net, target = QNet(), QNet()
    target.load_state_dict(net.state_dict())
    opt = torch.optim.Adam(net.parameters(), lr=LR)
    replay = Replay(BUFFER, rng)
    eps_steps = EPS_FRACTION * EPISODES * DECISIONS_PER_EPISODE
    decisions, updates, best = 0, 0, float("inf")
    log = []
    MODEL_DIR.mkdir(exist_ok=True)
    RESULTS_DIR.mkdir(exist_ok=True)
    log_file = RESULTS_DIR / f"training_seed{seed}.json"

    def epsilon():
        return max(EPS_END, EPS_START - (EPS_START - EPS_END) * decisions / eps_steps)

    for ep in range(EPISODES):
        t0 = time.time()
        traffic_seed = 1000 * (seed + 1) + ep          # never an evaluation or validation seed
        routes = demand.write_routes(TRAIN_SCENARIO, traffic_seed)
        env = JunctionEnv(routes, traffic_seed, f"{VARIANT}_train{seed}")

        def choose(obs):
            if rng.random() < epsilon():
                return int(rng.integers(N_PHASES))
            with torch.no_grad():
                return int(net(torch.from_numpy(obs)).argmax())

        ep_reward, losses = 0.0, []
        for o, a, r, d, o2 in run_episode(env, choose):
            replay.add(o, a, r, d, o2)
            decisions += 1
            ep_reward += r
            if replay.n >= WARMUP:
                ob, ac, rw, dc, ob2 = replay.sample(BATCH)
                with torch.no_grad():
                    best_next = net(ob2).argmax(1, keepdim=True)
                    y = rw + dc * target(ob2).gather(1, best_next).squeeze(1)
                q = net(ob).gather(1, ac.unsqueeze(1)).squeeze(1)
                loss = nn.functional.smooth_l1_loss(q, y)
                opt.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(net.parameters(), 10.0)
                opt.step()
                losses.append(loss.item())
                updates += 1
                if updates % TARGET_EVERY == 0:
                    target.load_state_dict(net.state_dict())
        train_score = env.score()
        row = {"seed": seed, "episode": ep, "traffic_seed": traffic_seed, "decisions": decisions,
               "train_valid": train_score["valid"], "epsilon": epsilon(),
               "episode_reward": ep_reward, "train_delay": train_score["mean_delay"],
               "train_max_wait": train_score["max_wait"],
               "loss": float(np.mean(losses)) if losses else None, "seconds": time.time() - t0}
        if (ep + 1) % VALIDATE_EVERY == 0:
            val = [v for sc, s in VALIDATION for v in evaluate(net, sc, (s,), f"{VARIANT}_val{seed}")]
            row["val_delay"] = float(np.mean([v["mean_delay"] for v in val]))
            row["val_max_wait"] = float(np.mean([v["max_wait"] for v in val]))
            row["val_valid"] = all(v["valid"] for v in val)
            if row["val_valid"] and row["val_delay"] < best:
                best = row["val_delay"]
                torch.save(net.state_dict(), MODEL_DIR / f"dqn_seed{seed}.pt")
                row["saved"] = True
        log.append(row)
        print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}), flush=True)
        log_file.write_text(json.dumps(log, indent=1), encoding="utf-8")
    if best == float("inf"):
        # No valid validation round: save the last network so the seed can still be evaluated.
        torch.save(net.state_dict(), MODEL_DIR / f"dqn_seed{seed}.pt")
        log[-1]["saved"] = "last episode, no valid validation round"
        log_file.write_text(json.dumps(log, indent=1), encoding="utf-8")
    return seed, best


def load(seed):
    net = QNet()
    net.load_state_dict(torch.load(MODEL_DIR / f"dqn_seed{seed}.pt"))
    net.eval()
    return net


if __name__ == "__main__":
    seeds = [int(a) for a in sys.argv[1:]] or list(TRAIN_SEEDS)
    if len(seeds) == 1:
        print(train(seeds[0]))
    else:
        with ProcessPoolExecutor(max_workers=min(WORKERS, len(seeds))) as pool:
            for seed, best in pool.map(train, seeds):
                print(f"seed {seed}: best validation delay {best:.1f} s/veh")
