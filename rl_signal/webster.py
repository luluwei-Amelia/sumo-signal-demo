"""Fixed-time plan by Webster's method, designed on the training day's peak hour.

    C0 = (1.5 L + 5) / (1 - Y)        optimal cycle
    g_i = (C0 - L) * y_i / Y           effective green of phase i

L is the lost time per cycle, LOST_PER_PHASE for each of the 4 phases (start-up loss plus the
unused part of the 3 s yellow; 4 s is the usual textbook value). Displayed green is effective
green + lost time - yellow, at least MIN_GREEN. One plan is used for the whole morning, as a
time-of-day AM-peak plan would be.

Usage: python webster.py      prints the plan and writes network/webster.tll.xml
"""
from build_net import GREEN_PHASES, green_states, yellow_state
from common import MIN_GREEN, NET_DIR, NET_FILE, TLS_ID, TRAIN_DAY, YELLOW
from demand import SAT_FLOW, TURN_SHARE, peak_hour, raw_profile, scale_factor

LOST_PER_PHASE = 4.0
PROGRAM_ID = "webster"
TLL_FILE = NET_DIR / "webster.tll.xml"


def phase_ratios(flows):
    """Critical lane flow ratio y of each green phase (same lane split as demand.flow_ratio_y)."""
    ys = []
    for axis, dirs in GREEN_PHASES:
        approaches = ("N", "S") if axis == "NS" else ("E", "W")
        if dirs == "ls":
            ys.append(max(flows[a] * (TURN_SHARE["l"] + TURN_SHARE["s"]) / 2 / SAT_FLOW["ls"] for a in approaches))
        else:
            ys.append(max(flows[a] * TURN_SHARE["r"] / SAT_FLOW["r"] for a in approaches))
    return ys


def plan():
    hour, k = peak_hour(raw_profile(TRAIN_DAY))
    s = scale_factor()
    flows = {a: float(v[0]) * s for a, v in hour.items()}
    ys = phase_ratios(flows)
    big_y = sum(ys)
    lost = LOST_PER_PHASE * len(ys)
    cycle = (1.5 * lost + 5) / (1 - big_y)
    greens = [max(MIN_GREEN, round((cycle - lost) * y / big_y + LOST_PER_PHASE - YELLOW)) for y in ys]
    return {"peak_bin": k, "flows": flows, "y": ys, "Y": big_y, "L": lost, "C0": cycle,
            "greens": greens, "cycle": sum(greens) + YELLOW * len(greens)}


def write_tll(greens):
    states = green_states(NET_FILE)
    phases = []
    for i, (g, s) in enumerate(zip(greens, states)):
        yellow = yellow_state(s, states[(i + 1) % len(states)])
        phases.append(f'        <phase duration="{g}" state="{s}"/>')
        phases.append(f'        <phase duration="{YELLOW}" state="{yellow}"/>')
    head = f'<additional>\n    <tlLogic id="{TLS_ID}" type="static" programID="{PROGRAM_ID}" offset="0">\n'
    TLL_FILE.write_text(head + "\n".join(phases) + "\n    </tlLogic>\n</additional>\n", encoding="utf-8")


if __name__ == "__main__":
    p = plan()
    write_tll(p["greens"])
    print(f"peak hour starts at bin {p['peak_bin']}, flows (veh/h) "
          + ", ".join(f"{a} {q:.0f}" for a, q in p["flows"].items()))
    print("y per phase", [round(y, 3) for y in p["y"]], f"Y {p['Y']:.3f}  L {p['L']:.0f} s  C0 {p['C0']:.1f} s")
    print("greens", p["greens"], "cycle", p["cycle"], "s")
