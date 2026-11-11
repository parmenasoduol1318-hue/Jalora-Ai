import type { SVGProps, ReactElement } from "react";

/**
 * Icon set for the left sidebar navigation.
 * Uses stroke-based SVG icons (fill:none, stroke:currentColor) matching the
 * neon-futuristic design.  All icons use viewBox="0 0 24 24" for consistent
 * 20px rendering.
 */

export function SidebarIcons() {
  return null;
}

const Icon = (path: ReactElement) => (props: SVGProps<SVGSVGElement>) =>
  (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      {path}
    </svg>
  );

export const navIcons: Record<string, (props: SVGProps<SVGSVGElement>) => ReactElement> = {
  chat: Icon(<path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L4 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 0 7.6-9.3 8.4 8.4 0 0 1 8.4-3v8z" />),

  recents: Icon(<><path d="M12 8v4l3 3M5.5 12a9 9 0 1 1 18 0 9 9 0 0 1-18 0z" /></>),

  dashboard: Icon(<><rect x="3" y="3" width="8" height="8" rx="1" /><rect x="13" y="3" width="8" height="5" rx="1" /><rect x="13" y="10" width="8" height="11" rx="1" /><rect x="3" y="13" width="8" height="8" rx="1" /><rect x="3" y="8" width="6" height="6" /><rect x="15" y="15" width="6" height="6" /></>),

  tasks: Icon(<><rect x="3" y="4" width="18" height="17" rx="2" /><path d="M8 2v4M16 2v4M7 12l-1 1-3 3" /></>),

  schedules: Icon(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l4 2" /></>),

  calendar: Icon(<><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 9h18" /></>),

  memory: Icon(<><path d="M14 2a2 2 0 1 1-4 0h4z" /><path d="M5 13a7 7 0 0 1 14 0c0 2.9-2.6 4.8-5 4.8-1.5 0-3.2-.7-4.1-1.8A4.8 4.8 0 0 1 7 12V9" /></>),

  workspace: Icon(<><path d="m8 17 5-5-5-5" /><path d="m16 7v10" /></>),

  documents: Icon(<><path d="M5 7h14v12a2 2 0 1 1-2 2H7a2 2 0 0 1-2-2Z" /><path d="M14 3h2v4h-2" /><path d="M4 13l4-4 4 4" /></>),

  integrations: Icon(<><circle cx="12" cy="12" r="9" /><path d="M12 7v5l4 2" /></>),

  settings: Icon(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 1 0 0 12 1.65 1.65 0 0 0 0-12Z" /><path d="M12 1v6m0 10v6M4.2 12h6m10 0h6" /></>),

  history: Icon(<><circle cx="12" cy="12" r="9" /><path d="M3 12a9 9 0 0 1 5-8.5M12 7v5l4 2" /></>),

  creative: Icon(<><rect x="2" y="2" width="20" height="20" rx="2" /><path d="M7 2v20M17 2v20M2 12h20M2 7h5M17 7h5M2 17h5M17 17h5" /></>),
};
