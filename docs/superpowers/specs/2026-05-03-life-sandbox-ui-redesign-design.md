# Life Sandbox UI Redesign — Design Spec

**Date:** 2026-05-03  
**Project:** AG2 Hackathon — Life Sandbox  
**Scope:** Frontend redesign of `life-sandbox/frontend.html`

---

## 1. Design direction

**Cinematic RPG** — the UI feels like starting a video game, not filling out a form. The user is the protagonist. Every screen is a full-moment experience with a clear emotional beat. The multi-agent pipeline is a spectacle, not a spinner.

**Visual language:**
- Background: deep space dark (`#07090f`) with a persistent indigo radial glow at the top
- Accent: indigo `#6366f1` → purple `#8b5cf6` gradient
- Typography: Inter, 900 weight for headlines, tight negative letter-spacing
- Subtle starfield (80 animated stars) throughout all screens
- Orbiting ring animation on landing only, fades away on step entry

---

## 2. App structure

Single `frontend.html` file. Six logical screens driven by JS — no routing, no page reloads. Transitions are fade + slide (forward = slide up, back = slide down, 450ms ease).

| # | Screen | Purpose |
|---|--------|---------|
| 1 | Landing | Hero moment, LinkedIn import entry point |
| 2 | Stage | Step 1 of 6 — current life stage |
| 3 | Field | Step 2 of 6 — field of study |
| 4 | Location | Step 3 of 6 — preferred location |
| 5 | Risk | Step 4 of 6 — risk tolerance slider |
| 6 | Ambition | Step 5 of 6 — ambition slider |
| 7 | Notes | Step 6 of 6 — freeform constraints + preferences |
| 8 | Simulation | Live agent pipeline view + optional MBTI sidebar |
| 9 | Results | Ranked paths with character costumes + CTA |

---

## 3. The walking figure

A persistent animated character walks along a road at the bottom of every screen. This is the single most distinctive visual element.

**Road:** Full-width SVG strip at the bottom of the viewport. Dark asphalt background (`#131928`), subtle dashed center line, indigo edge glow.

**Walker:** Emoji character (`🧑‍💻` on landing, then context-appropriate per step) positioned absolutely on the road. Animates left-to-right as the user progresses:

- When advancing: walking animation plays (bounce + tilt, `steps(2)` keyframes) while `left` CSS transitions to the new position (0.8s cubic-bezier)
- When idle: gentle bob animation
- On arrival: sparkle (`✨`) spawns at destination, then fades
- Footprint (`👣`) briefly appears at the departure point and fades out

**Milestone flags:** Emoji markers spaced along the road at each step's destination. They dim (`opacity: 0.3`) once passed, glow brightly at the current position.

**Walker positions by screen** (% of viewport width):

| Screen | Position | Walker emoji |
|--------|----------|-------------|
| 1 Landing | 8% | 🧑‍💻 |
| 2 Stage | 18% | 🧑‍🎓 |
| 3 Field | 30% | 🤔 |
| 4 Location | 42% | ✈️ |
| 5 Risk | 56% | 🎲 |
| 6 Ambition | 69% | 🔥 |
| 7 Notes | 80% | 💬 |
| 8 Simulation | 90% | 🔮 |
| 9 Results | 94% | 🎉 |

**Companion bubble:** A speech bubble floats just above the walker with a context-aware message per screen. Rerenders with a fade-up animation on each screen transition.

---

## 4. Screen-by-screen design

### Screen 1 — Landing

Full-screen hero. Three concentric orbital rings (SVG circles) rotate around the center at different speeds and directions. Each ring has a glowing dot riding it.

**Layout (centered, stacked):**
1. Badge pill: `AG2 Multi-Agent · 5 specialists` with a pulsing dot
2. Headline: *"Your life, your rules."* — 68px, 900 weight, white→indigo gradient text
3. Subtext: one sentence explaining the 5-agent simulation
4. **LinkedIn URL input** (hero CTA): large input box with a LinkedIn link icon, "Import →" button inline
5. Divider: `or start fresh`
6. Secondary CTA: `Fill in manually →` (outlined button)

**LinkedIn import behavior:** If a URL is pasted and Import is clicked, skip Screen 2 (stage) and pre-fill the field input on Screen 3. For the hackathon MVP, the URL is stored but not actually parsed — the step still asks for field/location/etc. manually. A future backend integration can scrape the profile.

---

### Screens 2–7 — Step-by-step profile

Shared layout: progress bar at top (fills from 0% → 100% across 7 steps), content centered in a `540px` max-width shell, navigation at the bottom.

**Progress bar:** 2px height, indigo→purple gradient, smooth width transition.

**Step dots:** 7 small circles below the content, current step highlighted as an elongated pill, completed steps dimmed indigo.

**Screen 2 — Stage:** Three large choice cards in a row (High school / Undergrad / Recent grad), each with emoji, label, sublabel. Tapping highlights with indigo border + glow.

**Screen 3 — Field:** Single text input, full width.

**Screen 4 — Location:** Single text input, full width.

**Screen 5 — Risk tolerance:** Giant `0.00`–`1.00` value displayed at 68px, color shifts from cool indigo (safe) to warm orange (risky) as slider moves. Custom-styled range input with indigo fill track.

**Screen 6 — Ambition:** Same giant slider pattern. Labels: "Stable plateau" → "Optimize for growth".

**Screen 7 — Notes:** Textarea, 3 rows minimum. Placeholder: example constraints and goals. CTA button reads "Simulate my life 🚀".

---

### Screen 8 — Simulation

**Left/main area:** Title "Simulating your futures…" with elapsed-time subtext. Five agent rows animate sequentially:
- State transitions: `waiting` → `running` (indigo border + background, chip animates) → `done` (green border + background, checkmark chip)
- Agents run in order: Coordinator first, then Career/Finance/Risk in sequence (displayed as parallel), then Decision
- After all 5 complete, auto-advance to Screen 9 after 700ms delay

**Walker behavior on Screen 8:** Walker reaches near the end of the road (90%) and performs an extended idle-bob while agents run. When agents complete, walker sprints to 94% with a faster walk animation.

**Optional MBTI sidebar** (future enhancement, not in MVP): A right-side panel with 3 personality questions appears during simulation. Answers feed into the decision agent's utility weighting. Not implemented for hackathon but the layout reserves space.

---

### Screen 9 — Results

**Header:** "Your 3 simulated futures" headline. Subtext shows the user's risk/ambition values and detected personality type (if MBTI implemented).

**Path cards:** Stacked vertically, animate in with a `translateX(-12px)` → `translateX(0)` slide with staggered delays. Each card shows:
- Career-specific emoji character (the "costume" — same person, different future)
- Path name and type
- Location and Year-1 compensation
- Ruin probability
- Utility score (right-aligned, large)

Rank #1 card gets indigo border + background tint.

**CTA:** "Choose [Path] → Get my roadmap" — full-width primary button that triggers Screen 10 (roadmap detail, not in hackathon MVP).

---

## 5. Character system

Two roles, one character:

**Role A — Journey companion** (Screens 1–8): The walking figure on the road. Context-appropriate emoji per screen, speech bubble with a short phrase.

**Role B — Career costume** (Screen 9): The path result cards each show the user's future self dressed for that career (🧑‍💻 Big Tech, 🧑‍🔬 Quant, 🧑‍🚀 Founder, etc.). Same emotional beat as a Sims character choosing an outfit.

---

## 6. Animation inventory

| Element | Animation | Trigger |
|---------|-----------|---------|
| Landing badge | Fade + slide up | Page load, 200ms delay |
| Landing title | Fade + slide up | Page load, 400ms delay |
| Landing CTA | Fade + slide up | Page load, 800ms delay |
| Orbital rings | Continuous rotation (18s / 30s / 45s) | Always on landing |
| Orbital dot glow | CSS box-shadow, always on | Always |
| Star field | Opacity twinkle (2–6s each, randomized) | Always |
| Screen transition (forward) | Fade + translateY(-24px) exit, translateY(20px)→0 enter | `advance()` |
| Screen transition (back) | Reverse direction | `retreat()` |
| Walker movement | `left` CSS transition, 0.8s cubic-bezier | Every screen change |
| Walker walking | Bounce + tilt, `steps(2)`, 0.35s | During movement |
| Walker idle | Gentle bob, 2s ease-in-out | When stationary |
| Footprint | Fade in → out, 1.5s | On departure |
| Sparkle | Scale + float up, 0.6s | On arrival |
| Milestone flag | Scale pop (0.8 → 1.2 → 1.0) | On reaching milestone |
| Companion bubble | Fade + slide up, 0.4s | Every screen change |
| Slider value | Color interpolation (indigo → orange) | On input |
| Progress bar | Width transition, 0.6s ease | Every step advance |
| Agent rows | Border + background color transition, 0.4s | Simulation sequence |
| Agent chip | Opacity blink, 1.2s infinite | While running |
| Result cards | Slide in from left, staggered 130ms | Screen 9 entry |

---

## 7. Implementation notes

**Single file:** All HTML, CSS, and JS remain in `life-sandbox/frontend.html`. No build step, no npm, no framework. Vanilla JS + CSS animations only.

**Screen management:** `goTo(n, forward)` function handles all transitions. Each screen is `position:absolute; opacity:0; pointer-events:none` by default, with `.active` class making it visible.

**Walker:** Positioned absolutely inside `#path-scene` div at the bottom of `#app`. The `left` property is set via JS per screen; CSS `transition: left 0.8s cubic-bezier(0.4, 0, 0.2, 1)` handles the movement.

**Responsive:** Screens are centered with `max-width` constraints. Road scene uses `viewBox` for SVG scaling. Font sizes use `clamp()` for fluid scaling between mobile and desktop.

**No external JS dependencies.** Chart.js (already in the project) used for salary curve sparklines on the result cards.

---

## 8. MVP scope (hackathon)

**In scope:**
- All 9 screens with animations
- Walking figure with full path scene
- LinkedIn URL input (stored, not parsed)
- Step-by-step form (screens 2–7)
- Simulation view wired to real SSE backend
- Results screen with career-costume characters

**Deferred:**
- LinkedIn profile parsing (backend scraper)
- MBTI sidebar during simulation
- Screen 10: roadmap detail
- AI-generated life picture (image generation)
- Side-by-side career comparison table

---

## 9. File reference

```
life-sandbox/
└── frontend.html    ← single file to rewrite
```

Mockups committed to `.superpowers/brainstorm/` (not tracked in production git).
