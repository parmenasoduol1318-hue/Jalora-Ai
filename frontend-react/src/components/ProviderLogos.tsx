/* ProviderLogos — inline SVG icons for integrations & AI providers.
   Every icon uses currentColor so dark/light theme vars apply automatically. */
import type { SVGProps } from "react";
import { type JSX } from "react";

type IconFn = (p: SVGProps<SVGSVGElement>) => JSX.Element;

const logos: Record<string, IconFn> = {
  // --- Communication ---
  gmail: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M16.5 12a4.5 4.5 0 1 1-9 0 4.5 4.5 0 0 1 9 0Z" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M2.25 6.75c0 1.37.5 2.64.9 3.86A9.75 9.75 0 0 0 9.75 18c.34.06.68.09 1.02.09A9.75 9.75 0 0 0 19.75 8.25V6a2.25 2.25 0 0 0-2.25-2.25H16.5c-.39 0-.75.06-1.09.17L6.09 8.1c-1.15.37-2.17.96-3.05 1.82a.75.75 0 0 0 .51 1.28h.04c1.16 0 2.2-.4 3.01-1.09l.79-.58a.75.75 0 0 1 1 .08l3.5 3.5a.75.75 0 0 1 0 1.06l-1.5 1.5a.75.75 0 0 1-1.06 0l-3.5-3.5a.75.75 0 0 1 .08-1l.58-.79Zm0 0v-.01" />
    </svg>
  ),
  twilio: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path fillRule="evenodd" clipRule="evenodd"
        d="M12 2C7.58 2 4 5.19 4 9.25c0 4.04 3.58 7.25 8 7.25s8-3.21 8-7.25C20 5.19 16.42 2 12 2ZM2 9.25C2 6.6 5.19 4 9 4h6c3.81 0 7 2.6 7 5.25v5.5A6.75 6.75 0 0 1 15.5 16v-.75a3.25 3.25 0 0 0-6.5 0V16A6.75 6.75 0 0 1 2 14.75v-5.5Z" />
      <path d="M9.5 10a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Zm9 0a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0Z" />
    </svg>
  ),
  telnyx: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M12 2 3.5 6.5v11L12 22l8.5-4.5v-11L12 2Zm0 2.31 6.2 3.28v8.82L12 19.69 5.8 16.41V7.59L12 4.31Z" />
      <path d="m12 8.2 3.6 1.9v3.8L12 15.8l-3.6-1.9v-3.8L12 8.2Z" />
    </svg>
  ),
  slack: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path fillRule="evenodd" clipRule="evenodd"
        d="M5.25 3a.75.75 0 0 0-1.5 0v8.25H3a.75.75 0 0 0 0 1.5h.75v.75a.75.75 0 0 0 1.5 0v-.75h6V3a.75.75 0 0 0-.75-.75H5.25Zm0 8.25V9h-1.5v2.25h1.5Zm-1.5 7.5h1.5v.75a.75.75 0 0 0 1.5 0v-1.5a.75.75 0 0 0-1.5 0v.75Zm11.25-5.25H18a2.25 2.25 0 1 0-.75-4.42A3.75 3.75 0 0 1 15 11.75h-.75a.75.75 0 0 0 0 1.5Zm4.5 3.75h.75a.75.75 0 0 0 0-1.5h-2.25V14.25h2.25v-.75a2.25 2.25 0 1 0-4.5 0v.75h2.25Z" />
    </svg>
  ),
  // --- Code / Dev ---
  github: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path fillRule="evenodd" clipRule="evenodd"
        d="M12 0C5.37.03 0 5.4 0 12.03c0 5.28 3.44 9.76 8.21 11.35.6.11.82-.26.82-.58v-1.86c-3.34.73-4.04-1.6-4.04-1.6C5.22 20.85 4.56 20.5 4.56 20.5c-1.34-.91.1-.89.1-.89 1.48.1 2.25 1.52 2.25 1.52 1.31 2.24 3.44 1.6 4.29 1.22.13-.95.49-1.6.9-1.96-3.15-.37-6.47-1.58-6.47-7.05 0-1.55.55-2.83 1.46-3.82-.15-.37-.63-1.85.13-3.85 0 0 1.19-.38 3.91 1.48A13.6 13.6 0 0 1 12 5.34c1.2.01 2.42.17 3.55.52 2.72-1.86 3.91-1.48 3.91-1.48.76 2 .28 3.48.14 3.85.91 1 1.46 2.27 1.46 3.82 0 5.48-3.33 6.68-6.49 7.03.51.44.96 1.31 1.09 2.65.99-.77 1.79-1.73 2.47-2.84 0-2.21.06-4.02.06-4.56 0-5.64-3.74-8.43-8.43-8.43-.5 0-1 .05-1.5.14 0 0-2.5.94-3.5 2.93a12.3 12.3 0 0 0-1.66-1.81 1.5 1.5 0 0 0-.28-.06h-.02Z" />
    </svg>
  ),
  notion: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M1 1.5A1.5 1.5 0 0 1 2.5 0h8A1.5 1.5 0 0 1 12 1.5v8h5.5a1.5 1.5 0 0 1 0 3h-5.5v6a1.5 1.5 0 0 1-1.5 1.5h-1A1.5 1.5 0 0 1 6 18.5v-6H1V1.5Z" />
      <path fillRule="evenodd" clipRule="evenodd"
        d="M13 0h8A1.5 1.5 0 0 1 22.5 1.5V7.5a1.5 1.5 0 0 1-1.5 1.5h-1.5V8a1.5 1.5 0 0 0-1.5-1.5H15V1.5A1.5 1.5 0 0 1 13 0Zm0 7.5V6h6v7.5a1.5 1.5 0 0 1-1.5 1.5H13V7.5Z" />
    </svg>
  ),
  spotify: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M12 0C5.4 0 0 5.4 0 12C0 17.6 5.4 22 12 22C18.6 22 24 17.6 24 12C24 5.4 18.6 0 12 0ZM5.5 11.25c0-.42.33-.75.75-.75.19 0 .38.06.53.16l5.25 3.25c.12.08.26.16.43.16.41 0 .75-.34.75-.75 0-.18-.07-.35-.2-.47L8.34 7.6 8.19 7.5c-.42 0-.75-.34-.75-.75 0-.2.07-.38.2-.52l2.34-3.6c.26-.4.86-.55 1.28-.32.42.23.61.74.51 1.17L10.47 14c-.04.16-.11.31-.21.44l-3.7 5.64c-.26.4-.85.52-1.26.3-.41-.22-.62-.74-.5-1.17l1.95-5.27c.53-1.42 2.17-1.9 3.44-.98.68.49 1.55 1.25 1.89 2.12.14.36.23.75.23 1.15v.01c0 .42-.34.75-.75.75-.19 0-.37-.06-.51-.16l-.28-.18-5.25-3.25c-.32-.2-.54-.51-.54-.89Z" />
    </svg>
  ),
  trello: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M2 6a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v12a4 4 0 0 1-4 4H6a4 4 0 0 1-4-4V6Zm14 0a4 4 0 0 1 4-4h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-2a4 4 0 0 1-4-4V6Z" />
      <path fillRule="evenodd" clipRule="evenodd"
        d="M4 8a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2H4Zm14 0a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2h-2Z" />
    </svg>
  ),
  dropbox: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M15.91 14.08 12 17l-3.91-2.93A3.5 3.5 0 0 1 6 12.5V8.5a3 3 0 1 1 6 0v4.08Zm0 0L12 19l-3.91-2.93a3.5 3.5 0 0 1 1-6.3 3.5 3.5 0 0 1 1 6.3Z" />
      <path d="M6 8.5a3 3 0 1 1 6 0v4.08l-3.91-2.93A3 3 0 0 0 6 8.5Z" />
      <path d="M12 12.58 8.09 10.47a3 3 0 0 1 3.91 0l3.91 2.93v-4.08a3 3 0 1 0-6 0v4.08Z" />
    </svg>
  ),
  // --- Payments & Travel ---
  stripe: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 9.5h18v3H3z" />
      <circle cx="8" cy="7.5" r="1" />
      <circle cx="16" cy="7.5" r="1" />
    </svg>
  ),
  amadeus: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M4 20l8-16 8 16H4Z" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M11 12l2 2 3-3" />
    </svg>
  ),
  // --- AI Providers ---
  openai: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M6 12c0-4.42 3.58-8 8-8v2a6 6 0 0 0-6 6H6Zm8 0c0 4.42-3.58 8-8 8v-2a6 6 0 0 1 6-6h2Z" />
    </svg>
  ),
  anthropic: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M12 2L2 4v8c0 5 5 10 10 10s10-5 10-10V4l-10-2Z" />
      <path d="M9 12c.5.5 1 .9 1.5 1.2C10.8 13.4 11.5 13.5 12 13.5s1.2-.1 1.5-.8c.3-.7.3-1.4 0-2s-.7-1.3-1.5-1.3V7c0-.8-.3-1.5-.9-2C11 4.5 10.5 4 10 4c-1 0-2 .5-2.5 1.4C7 6.5 7 7.5 7 8.5v2c0 .8.7 1.5 1.5 1.5S10 10.9 10 10c.5.5 1 .7 1.4.8l-.4.4c-.3-.1-.7-.2-1-.2Z" />
    </svg>
  ),
  google: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M20.66 10.25c-.02-.78-.17-1.52-.4-2.2h3.49c.58 1.19.91 2.52.91 4 0 3.32-2.15 6.09-5.19 7.28l-.03-.03c-1.24 1.01-2.76 1.83-4.37 2.34.08-.68.13-1.41.13-2.15v-.02c0-1.64-.18-3.23-.5-4.73h8.04c-.01.01 0-.01.01-.02Z" />
      <path d="M13.02 15.46c1.78-.28 3.17-1.66 3.56-3.35h-7.16c-.54 0-1.07-.1-1.55-.28.01.03.03.05.03.08 0 2.78 1.85 5.11 4.37 5.9Z" />
    </svg>
  ),
  groq: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2Zm5 10h-2.05c-.64 0-1.05-.56-1-1.2V8a1 1 0 1 0-2 0v2.75c0 .41-.12.8-.35 1.12l-.54.8a3.5 3.5 0 0 0 4.7 1.38l2.12-1.42a.75.75 0 0 0-.92-1.08l-.3.27Z" />
    </svg>
  ),
  deepseek: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <circle cx="12" cy="12" r="8" strokeWidth="2" />
      <path strokeLinecap="round" strokeWidth="2" stroke="currentColor" fill="none"
        d="M12 4v8l4 4" />
    </svg>
  ),
  mistral: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M6 18h12v-2H6v2Zm0-4h12v-2H6v2Z" />
    </svg>
  ),
  openrouter: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M3 12h2m14 0h2M7 8l-4 4 4 4m10-8 4 4-4 4M7 5l-2 7 2 7m10-14 2 7-2 7" />
    </svg>
  ),
  ollama: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <circle cx="12" cy="12" r="8" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 8v4l2 2M8 12h8" />
    </svg>
  ),
  lmstudio: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M8 9h8M8 15h8M4 7V5a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v2" />
      <circle cx="9" cy="12" r="1" fill="currentColor" />
      <circle cx="15" cy="12" r="1" fill="currentColor" />
    </svg>
  ),
  jan: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M6 18L18 6M6 6l12 12" />
      <rect x="3" y="3" width="18" height="18" rx="3" />
    </svg>
  ),
  cohere: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M12 2L2 7v10l10 5 10-5V7l-10-5Z" />
      <path d="M12 12V2m0 0l5 3.5M12 12V2m0 0L7 5.5M12 12l5 3.5L12 18l-5-3.5L12 12Z" />
      <circle cx="12" cy="12" r="1.5" />
    </svg>
  ),
  cerebras: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 2L3 7v10l9 5 9-5V7l-9-5Z" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 7v5l3 3M12 12L9 9l3 3h3a3 3 0 0 1 0 6 3 3 0 0 1-6 0M9 9l3 3v5" />
    </svg>
  ),
  nvidia: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M3 8c5-5 13-5 18 0M3 12c4-4 10-4 14 0M6 16c2-2 5-2 7 0" />
      <circle cx="12" cy="12" r="2" />
    </svg>
  ),
  perplexity: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M9 12l2 2 4-4M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
    </svg>
  ),
  search: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <circle cx="10" cy="10" r="6" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 15 21 21" />
    </svg>
  ),
  calendar: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M8 2v3M16 2v3M3 9h18" />
    </svg>
  ),
  drive: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.61 5.64 5.36 8.04A5.994 5.994 0 0 0 12 20c2.04 0 3.87-.68 5.17-1.83l-.02-.02.02.01c.5-.44.85-1.03 1.05-1.75l.09-.27v-.01l-.03-.03.01.02-.01-.01Z" />
      <path d="M12 2L4 8v8l8 4 8-4V8l-8-6Z" />
      <path d="M12 2v6m8 10a5.2 5.2 0 0 1-4 1.92M4 14a5.2 5.2 0 0 1 4 1.92L4 16l4 4 8-4-4 2.08a5.2 5.2 0 0 0 4-1.92" />
    </svg>
  ),
  contacts: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <circle cx="12" cy="8" r="4" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M4 20c0-3.3 2.7-6 6-6h4c3.3 0 6 2.7 6 6" />
    </svg>
  ),
  maps: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M9 11a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M15 15a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M3 21l6-4v-6M9 11V5l3-2 3 2v6M15 11l6-4v6l-6 4-3-2" />
    </svg>
  ),
  weather: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 2v2m0 16v2M4.93 4.93 6.34 6.34M17.66 17.66l1.41 1.41M2 12h2m18 0h2" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 6a6 6 0 1 0 0 12 6 6 0 0 0 0-12Z" />
    </svg>
  ),
  translate: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M7.5 9.5 5 14.5l2.5 5M16.5 4.5 19 12l-2.5 9" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M12 2l4 4-4 4-4-4 4-4Z" />
    </svg>
  ),
  database: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <ellipse cx="12" cy="12" rx="8" ry="4" />
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M2 12c0-2.21.47-4.31 1.31-6.12M22 12c0-2.21-.47-4.31-1.31-6.12M3.31 5.88A9.93 9.93 0 0 1 12 4c2.65 0 5.12.84 7.19 2.22M3.31 18.12A9.93 9.93 0 0 0 12 20c2.65 0 5.12-.84 7.19-2.22M3.31 5.88v12.24M20.69 5.88v12.24" />
    </svg>
  ),
  redis: (p) => (
    <svg viewBox="0 0 24 24" fill="currentColor" {...p}>
      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2Zm0 18c-4.42 0-8-3.58-8-8s3.58-8 8-8 8 3.58 8 8-3.58 8-8 8Z" />
      <path d="M9 9h6v2H9V9Zm0 4h6v2H9v-2Z" />
    </svg>
  ),
  browser: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <path strokeLinecap="round" strokeLinejoin="round"
        d="M3 6h18M6 10h12M6 14h12M6 18h12" />
    </svg>
  ),
  // Fallback for unknown providers
  default: (p) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" {...p}>
      <circle cx="12" cy="12" r="9" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M9.09 9.09 12 12m0 0 2.91 2.91M12 12l-2.91 2.91M12 12l2.91-2.91" />
    </svg>
  ),
};

export function getLogo(id: string): IconFn {
  return logos[id] ?? logos.default;
}

export function ProviderLogo({ id, className }: { id: string; className?: string }) {
  const Icon = getLogo(id);
  return <Icon className={className} width={20} height={20} viewBox="0 0 24 24" />;
}
