import type { SVGProps } from "react";

/**
 * Premium futuristic AI assistant avatar.
 * Uses currentColor so it inherits the text color, with a gradient
 * accent on the outer ring that matches the screenshot palette
 * (deep blue #084389 → purple #5a329a).
 */
export function AssistantAvatar({
  size = 80,
  className,
  ...props
}: SVGProps<SVGSVGElement> & { size?: number | string }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 80 80"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      {...props}
    >
      {/* Outer gradient ring matching screenshot accent colors */}
      <defs>
        <linearGradient
          id="jalora-avatar-ring"
          x1="0%"
          y1="0%"
          x2="100%"
          y2="100%"
        >
          <stop offset="0%" stopColor="#084389" />
          <stop offset="100%" stopColor="#5a329a" />
        </linearGradient>
        <filter id="jalora-glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="3" result="coloredBlur" />
          <feMerge>
            <feMergeNode in="coloredBlur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      {/* Outer ring with glow */}
      <circle
        cx="40"
        cy="40"
        r="36"
        stroke="url(#jalora-avatar-ring)"
        strokeWidth="2.5"
        filter="url(#jalora-glow)"
      />

      {/* Inner background */}
      <circle cx="40" cy="40" r="31" fill="var(--bg-2)" />

      {/* Facial guide: two eyes and a smile — stylised as circuit-like lines */}
      {/* Left eye */}
      <path
        d="M26 34c0-3 2-5 5-5s5 2 5 5"
        stroke="currentColor"
        strokeWidth="2"
        fill="none"
        strokeLinecap="round"
        opacity="0.85"
      />
      {/* Right eye */}
      <path
        d="M44 34c0-3 2-5 5-5s5 2 5 5"
        stroke="currentColor"
        strokeWidth="2"
        fill="none"
        strokeLinecap="round"
        opacity="0.85"
      />
      {/* Smile */}
      <path
        d="M28 48c2 4 6 6 10 6s8-2 10-6"
        stroke="currentColor"
        strokeWidth="2.5"
        fill="none"
        strokeLinecap="round"
        opacity="0.7"
      />

      {/* Subtle sparkles around the avatar */}
      <circle cx="18" cy="22" r="1.2" fill="currentColor" opacity="0.4" />
      <circle cx="60" cy="20" r="1" fill="currentColor" opacity="0.3" />
      <circle cx="58" cy="58" r="1.5" fill="currentColor" opacity="0.35" />
      <circle cx="20" cy="58" r="0.8" fill="currentColor" opacity="0.3" />
    </svg>
  );
}
