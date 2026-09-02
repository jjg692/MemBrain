using System;
using Microsoft.Xna.Framework;
using Microsoft.Xna.Framework.Graphics;
using StardewValley;

namespace StardewMCPBridge
{
    /// <summary>
    /// A Farmer subclass that serves as an AI-controlled farmhand.
    /// Drives game mechanics (tools, combat, fishing, inventory) directly on the instance.
    /// Deliberately NOT added to Game1.otherFarmers — that table is the network-synced
    /// remote-player collection and Multiplayer.updateRoots() NPEs on a non-net-backed farmer.
    /// Invisible — the paired NPC handles all rendering.
    /// </summary>
    public class BotFarmer : Farmer
    {
        /// <summary>Marks this farmer as AI-controlled (not a real player).</summary>
        public bool IsBot { get; } = true;

        /// <summary>Prevent the shadow farmer from rendering — the NPC sprite is the visual.</summary>
        public override void draw(SpriteBatch b)
        {
            // No-op: companion NPC handles all rendering
        }

        public override void SetMovingUp(bool b)
        {
            if (!b) Halt();
            else moveUp = true;
        }

        public override void SetMovingRight(bool b)
        {
            if (!b) Halt();
            else moveRight = true;
        }

        public override void SetMovingDown(bool b)
        {
            if (!b) Halt();
            else moveDown = true;
        }

        public override void SetMovingLeft(bool b)
        {
            if (!b) Halt();
            else moveLeft = true;
        }

        // Simplified movement that doesn't reference Game1.player internally
        public new void tryToMoveInDirection(int direction, bool isFarmer, int damagesFarmer, bool glider)
        {
            bool canPass = currentLocation.isTilePassable(nextPosition(direction), Game1.viewport);
            if (canPass)
            {
                switch (direction)
                {
                    case 0: position.Y -= speed + addedSpeed; break;
                    case 1: position.X += speed + addedSpeed; break;
                    case 2: position.Y += speed + addedSpeed; break;
                    case 3: position.X -= speed + addedSpeed; break;
                }
            }
        }

        public void FaceToward(Vector2 targetTile)
        {
            Vector2 diff = targetTile * 64f - this.Position;
            if (Math.Abs(diff.X) > Math.Abs(diff.Y))
                this.FacingDirection = diff.X > 0 ? 1 : 3;
            else
                this.FacingDirection = diff.Y > 0 ? 2 : 0;
        }

        /// <summary>Restore farmer state for a new day.</summary>
        public void WakeUp()
        {
            this.isInBed.Value = false;
            this.sleptInTemporaryBed.Value = false;
            this.Stamina = this.MaxStamina;
            this.health = this.maxHealth;
        }

        /// <summary>Signal that this bot is ready for sleep/end-of-day. Prevents deadlock.</summary>
        public void SignalSleepReady()
        {
            this.isInBed.Value = true;
        }
    }
}
