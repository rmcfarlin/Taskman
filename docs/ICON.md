# Taskman icon

The icon pairs the Dark Teal tile with a folded mint-and-white checkmark. The broad silhouette is designed for Explorer, shortcuts, and small taskbar launchers.

![Taskman icon](../assets/taskman.png)

`assets/taskman.png` is the transparent full-resolution master. `assets/taskman.ico` contains 16, 20, 24, 32, 40, 48, 64, 128, and 256 pixel frames. Windows standalone builds embed those frames in the executable. Standalone downloads also include both artwork files for shortcut or terminal-profile customization.

The application remains a console TUI. Windows Terminal controls its own running-window icon and grouping; the executable icon brands Explorer and pinned launchers.

## Artwork provenance

Generated with the built-in ImageGen tool. The selected image was refined for its edge, satin finish, and true alpha transparency, then encoded into ICO without changing the artwork.

Original prompt: "Use case: logo-brand. Asset type: finished desktop application icon, square 1024x1024, one icon only, no presentation board or mockup. Design an exceptional, distinctive icon for Taskman, a fast keyboard-first Markdown task manager. Palette is its Dark Teal theme: very deep petrol teal #244D4F, charcoal #242424, luminous pale mint #9CCFC8, nearly white #F3FFFC. A single confident, thick, sculptural checkmark is the central mark, constructed like a precisely folded ribbon: the short left arm is pale mint, the long ascending right arm is almost white, with a small crisp folded teal facet at their join. Bold simple silhouette, generously weighted, immediately legible at 16px and 32px. The checkmark sits on a beautifully proportioned rounded-square dark teal tile, with subtle matte enamel depth and a restrained lighter top edge. Elegant geometric proportions, precise optical centering, generous clear space around the checkmark, no thin lines, no text, no letters, no checklist rows, no added symbols, no sparkles, no glow halo, no ornament. Front-on orthographic view, no perspective tilt. The tile fills about 88% of canvas width and height with gently rounded corners. Transparent background outside the tile, actual alpha transparency rather than a drawn checkerboard. Premium professional productivity app identity: calm, confident, refined, distinctive rather than a generic system check icon. Deliver the isolated finished icon at maximum clarity, no surrounding scene."

Final transparency pass: "Use case: background-extraction. This is an app icon that must have a genuinely transparent PNG alpha channel. Remove the gray-and-white checkerboard background entirely and replace it with fully transparent pixels (alpha=0). Preserve the dark teal rounded-square tile and the folded mint-and-white checkmark exactly as pictured. No redesign, no new colors, no text, no mockup. Keep the existing clear margin around the tile. Clean, smooth antialiased tile perimeter. The output must be an RGBA PNG with real transparency, not an image showing a transparency grid. Do not draw any background."
