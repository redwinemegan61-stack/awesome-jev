# Adaptive & Realtime UI

Use this category for interfaces where a Jev decision changes what the user sees or can do, live — per-element filtering and covering, canvases and editors that respond as you type, and UI generated from a decision. Games that render Jev probabilities live belong in Game & Simulation; this category is for the interface itself being the product.

## Submission format

```md
- [Name](URL) - Industry: one-sentence description of the Jev use case.
```

## Entries

- [typesafe-adblock](https://github.com/realZachi/typesafe-adblock) `{type: extension}` - Browser tooling: Chrome extension that asks Jev whether each DOM element is an ad, turning ad blocking into a stream of per-element typed questions.
- [unclutter](https://github.com/kitze/unclutter) - Browser tooling: WXT extension where Jev decides per page element whether it is clutter, removing it under reusable template rules.
- [sift](https://github.com/bohutang/sift) `{type: extension}` - Content labelling: Chrome extension that labels every post in an X timeline - substance, humour, chit-chat, promo, junk, or AI-written - with Jev decisions.
- [json-render](https://github.com/vercel-labs/json-render) - Generative UI: Vercel Labs' UI framework uses Jev in its compose path to pick which components and actions a rendered interface should contain.
- [PlotVeil](https://github.com/Dearest/plotveil) `{type: extension}` - Spoiler protection: Chrome extension that covers each YouTube comment while one Jev `Noul` question, batched 20 at a time, answers whether it reveals a concrete plot event of the video being watched or of another title the user protects, with the extension owning the 0.85 / 0.7 / 0.5 threshold and keeping the comment covered when the check fails.
- [jev-canvas](https://github.com/gaborishka/jev-canvas) - Multimodal UI: draw on a tldraw canvas by voice while pointing a webcam-tracked finger; on every partial transcript Jev answers eight typed questions (is it a command, is the sentence complete, action, shape, colour, target, place, size) and plain code gates them with thresholds, in English and Ukrainian, 300–550 ms per decision.
- [DWIM](https://github.com/rohit9mehta/dwim) - Desktop productivity: a macOS command palette that reads the frontmost app's menu tree through the accessibility API, asks Jev one `Noul` per menu item against the user's plain-language request, and presses the top match when it clears a probability threshold, falling back to a ranked list otherwise and never auto-running destructive items.
- [SemanticSpace](https://semanticspace.dev/) - Semantic mapping: places phrases in 2D by asking Jev how strongly each one relates to two chosen axis concepts and using those scores as coordinates.
- [shapeshift](https://github.com/anishfn/shapeshift) - Input: one text box that morphs into the right UI as you type, asking Jev which control the sentence calls for, and running offline.
- [Jevcast](https://github.com/RyanErkal/jevcast) - Desktop productivity: native macOS launcher and window manager that uses Jev to match natural-language window and action commands to known application workflows with local response caching.
