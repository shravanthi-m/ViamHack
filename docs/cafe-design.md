# Varista café design

Visual reference: https://enlynyc.com/ (reviewed September 19, 2026).
The design uses its monochrome lifestyle direction, centered wordmark, thin
uppercase typography, and a quiet ivory counter section.
Varista's copy, branding, and generated images are original.

The UI contains only the front-page hero and the open counter: a camera scene,
three preset task buttons (Pour signature drink, Reset, Shake), and one custom-task
input. The gallery, story, voice controls, recipe card, and run timeline are removed.
Reset means placing the held item on the table and returning home. Shake means
finding the shaker, shaking it, and returning it to its original location.

All requests use the existing request endpoint. Only the signature pour has an
execution integration; Reset, Shake, and unsupported custom tasks explicitly report
that they are not connected. Existing operator gates are preserved. Live view starts
without automatic identification; the UI keeps just the camera scene.

## Image assets

Generated with the built-in imagegen tool; saved in the project:

- `runtime/observer_web/cafe-lifestyle.png`
The earlier colorful `cafe-editorial.png` artwork is no longer used anywhere on the page, per user preference.

### Lifestyle prompt

Use case: photorealistic-natural. Asset type: wide full-bleed homepage hero photograph for a sophisticated downtown New York cafe called Varista. Create an original black-and-white fashion editorial photograph, horizontal 16:9. Close cropped candid scene of an anonymous stylish customer in a textured oversized light cashmere sweater at a dark stone cafe counter, face entirely outside crop, one hand holding a clear ribbed glass iced latte with sumptuous sculptural whipped cream and cocoa dusting. A small polished steel spoon and another iced coffee on counter, elegant minimal architectural cafe interior blurred behind. Strong window light, rich film grain, tactile knit, cinematic deep charcoal shadows, realistic silver gelatin photograph. The glass and hand occupy right third, sweater in center-right, left half is moody dark negative space for a white website headline. No visible brand, no bag, no jewelry logos, no text, no watermarks. Luxurious restrained cool downtown energy, natural not staged advertising.

