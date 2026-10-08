# Robotics & Physical

Use this category for decisions that act on physical execution — arms, drones, fleets and edge hardware — and for measurements of Jev's latency and cost in the physical world. A simulated robot belongs here when the simulation is the testbed for a physical loop, which describes most of them today. Games and environments with no physical counterpart stay in Game & Simulation, and a harness whose only actuator is a software tool belongs in Agent Decisions.

## Submission format

```md
- [Name](URL) - Industry: one-sentence description of the Jev use case.
```

## Entries

- [robo-harness](https://github.com/grmkris/robo-harness) - Robotics: SO-101 arm workbench where a Jev decision runner picks bounded joint steps from typed candidate actions under a spend budget.
- [OmniJev](https://github.com/shapsider/OmniJev) - Embodied robotics: a Jev-style finite-choice interface that feeds dual-camera images and text to a self-hosted multimodal model and takes the next preset skill for a MuJoCo arm as one typed choice, where released episodes finish transfer, stack and barrier tasks in 13 decisions and 39 output tokens each, 208/208 non-audio probe requests answer correctly, unobservable inputs come back "insufficient evidence" instead of a guess, and four public benchmark pilots hold accuracy equal to a direct short answer while cutting decision latency 6.8–13.6× and total tokens 51–86% (audio input is wired in the client but rejected by the current backend).
- [Jev Robot](https://github.com/Hu-xiao-max/jev_robot) - Robotics control: a local decider-2b model picks the next skill for an AgileX PiPER arm through a Jev-style typed-choice interface while the target moves, with deterministic checks allowed to reject a choice but never to substitute another.
- [jev-drone](https://github.com/RomanSlack/jev-drone) - Robotics simulation: camera-only autonomous drone in MuJoCo that puts a Jev judgment model in the control loop at 2.5 Hz.
- [typesafe-jev-drone-demo](https://github.com/kxzk/typesafe-jev-drone-demo) - Simulation: Three.js drone simulator with a Python backend where Jev drives the navigation decisions.
- [jev-reflex-autonomy-lab](https://github.com/khordoo/jev-reflex-autonomy-lab) - Drone autonomy: a multi-drone lab where Jev supplies the reflex decisions, with an optional slower strategy layer guiding them.
- [RoboJEV](https://github.com/lykycy123/RoboJEV) - Robotics simulation: uses two-stage Jev `Choice` decisions over structured state to select intent and Cartesian motion/gripper commands for a Franka Panda in MuJoCo, rejecting malformed responses and checking task success independently through physics.
- [Jev for Physical AI](https://github.com/robokrunch/jev-physical-ai) - Fleet triage: runs Jev as the decision layer for a 10,000-robot warehouse fleet over 41 bilingual incident templates and publishes 0.527 s p50 latency, $24.57 per million decisions and 91.3% agreement with template labels, alongside a crossover against a self-hosted ModernBERT.
- [EmbodiedJev](https://github.com/FBddcz/embodied-jev) - Embodied robotics: MuJoCo decision workbench for a Franka Panda arm that compares TypeSafe Jev System One candidate decisions against reactive baselines and LLMs across pick, place, and obstacle tasks.

