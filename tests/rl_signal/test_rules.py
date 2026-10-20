"""Checks that the comparison is fair: signal rules, demand separation and the fixed-time plan."""
import shutil

import pytest

import build_net
import demand
import dqn
import webster
from common import (DECISION_INTERVAL, EVAL_SEEDS, MAX_GREEN, MAX_RED, MIN_GREEN, NET_FILE, TLS_ID,
                    TRAIN_SEEDS, YELLOW)

needs_sumo = pytest.mark.skipif(shutil.which("sumo") is None, reason="SUMO binary not installed")


def test_every_link_is_green_in_exactly_one_phase():
    states = build_net.green_states(NET_FILE)
    assert len(states) == 4
    for i in range(len(states[0])):
        assert sum(s[i] == "G" for s in states) == 1


def test_yellow_only_on_links_that_lose_green():
    a, b = build_net.green_states(NET_FILE)[:2]
    y = build_net.yellow_state(a, b)
    for ca, cb, cy in zip(a, b, y):
        assert cy == ("y" if ca == "G" and cb == "r" else "r")


def test_routes_are_reproducible_and_differ_by_seed():
    a = demand.write_routes("train", 100).read_text()
    assert a == demand.write_routes("train", 100).read_text()
    assert a != demand.write_routes("train", 101).read_text()


def test_training_never_sees_evaluation_or_validation_traffic():
    train_seeds = {1000 * (s + 1) + ep for s in TRAIN_SEEDS for ep in range(dqn.EPISODES)}
    val_seeds = set(dqn.VALIDATION_SEEDS) | set(dqn.RANDOM_VALIDATION_SEEDS)
    assert not train_seeds & set(EVAL_SEEDS)
    assert not train_seeds & val_seeds
    assert not val_seeds & set(EVAL_SEEDS)


def test_random_training_demand_stays_in_range():
    import numpy as np
    from common import RANDOM_LEVEL, RANDOM_SHARE
    lo, hi = RANDOM_LEVEL[0] * RANDOM_SHARE[0], RANDOM_LEVEL[1] * RANDOM_SHARE[1]
    for seed in range(20):
        f = demand.random_factors(np.random.default_rng(seed))
        assert all(lo <= v <= hi for v in f.values())
    a = demand.write_routes("random", 1000).read_text()
    assert a == demand.write_routes("random", 1000).read_text()
    assert a != demand.write_routes("train", 1000).read_text()


def test_scenarios_keep_the_same_scale():
    base = demand.profile("train")
    plus = demand.profile("plus20")
    flip = demand.profile("flip")
    assert all(abs(plus[a] / base[a] - 1.2).max() < 1e-9 for a in base)
    assert (flip["W"] == base["E"]).all() and (flip["N"] == base["S"]).all()


def test_webster_plan_is_sane():
    p = webster.plan()
    assert abs(p["Y"] - demand.TARGET_Y) < 1e-9
    assert all(g >= MIN_GREEN for g in p["greens"])
    assert 60 <= p["cycle"] <= 150


class AlwaysSwitch:
    """A controller that asks for a different phase at every decision."""

    def reset(self, env):
        pass

    def act(self, env, obs):
        return (env.phase + 1) % 4


class NeverSwitch:
    def reset(self, env):
        pass

    def act(self, env, obs):
        return env.phase


class Stubborn:
    """Always asks for phase 0: without the max-red rule, phases 2 and 3 would never run."""

    def reset(self, env):
        pass

    def act(self, env, obs):
        return 0


def signal_log(ctl, seconds=900):
    """Run a controller for a while and return the (time, state) of every signal change."""
    from env import JunctionEnv
    env = JunctionEnv(demand.write_routes("train", 100), 100, f"test_{type(ctl).__name__}", tripinfo=False)
    obs = env.reset()
    log = [(env.time(), env.conn.trafficlight.getRedYellowGreenState(TLS_ID))]
    set_state = env.conn.trafficlight.setRedYellowGreenState

    def logged(tls, state):
        log.append((env.time(), state))
        set_state(tls, state)

    env.conn.trafficlight.setRedYellowGreenState = logged
    ctl.reset(env)
    while env.time() < seconds:
        obs, _, _ = env.step(ctl.act(env, obs))
    env.close()
    return log


@needs_sumo
@pytest.mark.parametrize("ctl", [AlwaysSwitch(), NeverSwitch(), Stubborn()])
def test_min_green_max_green_and_yellow_are_enforced(ctl):
    log = signal_log(ctl)
    for (t0, s0), (t1, _) in zip(log, log[1:]):
        held = t1 - t0
        if "y" in s0:
            assert held == YELLOW
        else:
            assert MIN_GREEN <= held <= MAX_GREEN


@needs_sumo
def test_no_phase_stays_red_much_longer_than_max_red():
    log = signal_log(Stubborn(), seconds=1800)
    greens = [(t, s) for t, s in log if "y" not in s]
    states = build_net.green_states(NET_FILE)
    # the override acts at the next decision once the current green may end
    bound = MAX_RED + MIN_GREEN + DECISION_INTERVAL + YELLOW
    for s in states:
        starts = [t for t, g in greens if g == s] + [1800]
        assert starts[0] <= bound
        assert all(b - a <= bound + MAX_GREEN for a, b in zip(starts, starts[1:]))


@needs_sumo
def test_long_red_weight_only_adds_to_the_reward(monkeypatch):
    import env as env_module

    def total_reward(weight):
        monkeypatch.setattr(env_module, "LONG_RED_WEIGHT", weight)
        routes = demand.write_routes("train", 100)
        e = env_module.JunctionEnv(routes, 100, f"test_weight{weight}", tripinfo=False)
        obs = e.reset()
        total = 0.0
        while e.time() < 900:
            obs, r, _ = e.step(Stubborn().act(e, obs))
            total += r
        e.close()
        return total

    base, weighted = total_reward(0.0), total_reward(1.0)
    assert weighted < base          # Stubborn keeps phases red for long, so the weight applies
    assert total_reward(0.0) == base


@needs_sumo
def test_unfinished_vehicles_are_counted_not_dropped():
    from env import JunctionEnv
    env = JunctionEnv(demand.write_routes("plus20", 100), 100, "test_unfinished")
    obs = env.reset()
    ctl = Stubborn()
    env.end_time = 1800          # stop while vehicles are still on the road
    done = False
    while not done:
        obs, _, done = env.step(ctl.act(env, obs))
    score = env.score()
    assert score["unfinished"] > 0 and not score["valid"]
    routes = demand.write_routes("plus20", 100).read_text()
    departed_by_end = sum(1 for line in routes.splitlines()
                          if "<trip " in line and float(line.split('depart="')[1].split('"')[0]) <= 1800)
    assert score["vehicles"] >= departed_by_end - 1
