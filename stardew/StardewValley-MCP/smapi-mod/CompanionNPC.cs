using System;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewValley;

namespace StardewMCPBridge
{
    /// <summary>
    /// NPC subclass that corrects the sprite aspect ratio.
    /// Vanilla NPC.draw() uses a uniform float scale, which makes custom
    /// companion sprites look narrower than the multi-layered Farmer sprite.
    /// This override uses Vector2 scale to widen the sprite slightly.
    /// </summary>
    public class CompanionNPC : NPC
    {
        public float WidthScale { get; set; } = 1.3f;

        public CompanionNPC(AnimatedSprite sprite, Vector2 position, string defaultMap,
            int facingDir, string name, Texture2D portrait, bool eventActor)
            : base(sprite, position, defaultMap, facingDir, name, portrait, eventActor)
        {
        }

        public override void draw(SpriteBatch b, float alpha = 1f)
        {
            if (Sprite?.Texture == null || IsInvisible) return;

            float baseScale = Math.Max(0.2f, scale.Value) * 4f;
            Vector2 drawScale = new Vector2(baseScale * WidthScale, baseScale);

            Vector2 localPos = getLocalPosition(Game1.viewport);
            float widthOffset = GetSpriteWidthForPositioning() * 4 / 2;
            float heightOffset = GetBoundingBox().Height / 2;

            Vector2 screenPos = localPos + new Vector2(widthOffset, heightOffset);
            Vector2 origin = new Vector2(
                Sprite.SpriteWidth / 2f,
                Sprite.SpriteHeight * 3f / 4f
            );

            float layerDepth = Math.Max(0f,
                drawOnTop ? 0.991f : (float)StandingPixel.Y / 10000f);

            SpriteEffects effects = flip ||
                (Sprite.CurrentAnimation != null &&
                 Sprite.currentAnimationIndex < Sprite.CurrentAnimation.Count &&
                 Sprite.CurrentAnimation[Sprite.currentAnimationIndex].flip)
                ? SpriteEffects.FlipHorizontally
                : SpriteEffects.None;

            b.Draw(
                Sprite.Texture,
                screenPos,
                Sprite.SourceRect,
                Color.White * alpha,
                0f,
                origin,
                drawScale,
                effects,
                layerDepth
            );

            // Emote bubble (reuse vanilla logic position)
            if (isEmoting && !Game1.eventUp)
            {
                Vector2 emotePos = getLocalPosition(Game1.viewport);
                emotePos.Y -= 32 + Sprite.SpriteHeight * 4;
                b.Draw(Game1.emoteSpriteSheet, emotePos,
                    new Rectangle(CurrentEmoteIndex * 16 % Game1.emoteSpriteSheet.Width,
                        CurrentEmoteIndex * 16 / Game1.emoteSpriteSheet.Width * 16, 16, 16),
                    Color.White, 0f, Vector2.Zero, 4f, SpriteEffects.None, layerDepth + 0.0001f);
            }
        }
    }
}
