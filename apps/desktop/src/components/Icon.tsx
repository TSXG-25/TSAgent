import type { SVGProps } from "react";

export type IconName =
  | "activity"
  | "arrow-up-right"
  | "box"
  | "check"
  | "clock"
  | "code"
  | "file"
  | "folder"
  | "layers"
  | "play"
  | "plus"
  | "refresh"
  | "send"
  | "settings"
  | "shield"
  | "spark"
  | "x";

const iconPaths: Record<IconName, string> = {
  activity: "M3 12h3l2-7 4 14 2-7h7",
  "arrow-up-right": "M7 17 17 7M9 7h8v8",
  box: "m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z M4 7.5l8 4.5 8-4.5 M12 12v9",
  check: "m5 12 4 4L19 6",
  clock: "M12 7v5l3 2 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  code: "m8 9-4 3 4 3M16 9l4 3-4 3M14 5l-4 14",
  file: "M6 3h8l4 4v14H6z M14 3v5h5 M9 13h6M9 17h6",
  folder: "M3 6h7l2 2h9v10H3z",
  layers: "m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5",
  play: "m8 5 11 7-11 7V5Z",
  plus: "M12 5v14M5 12h14",
  refresh: "M20 11a8 8 0 1 0 1 5M20 5v6h-6",
  send: "m21 3-7.5 18-3.4-7.1L3 10.5 21 3ZM10.1 13.9 21 3",
  settings: "M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-1.7 1.7-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5v.2h-2.4v-.2a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1L8 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H6v-2.4h.2a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.9L7.3 8.6 9 6.9l.1.1a1.7 1.7 0 0 0 1.9.3 1.7 1.7 0 0 0 1-1.5v-.2h2.4v.2a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l-.1-.1 1.7 1.7-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.5 1h.2V14h-.2a1.7 1.7 0 0 0-1.5 1Z",
  shield: "M12 3 20 6v5c0 5.1-3.4 8.7-8 10-4.6-1.3-8-4.9-8-10V6l8-3Z M9 12l2 2 4-4",
  spark: "m12 2 1.6 6.4L20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6L12 2ZM19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16Z",
  x: "m6 6 12 12M18 6 6 18",
};

interface IconProps extends SVGProps<SVGSVGElement> {
  name: IconName;
  size?: number;
}

export function Icon({ name, size = 16, className = "", ...props }: IconProps) {
  return (
    <svg aria-hidden="true" className={`icon ${className}`} fill="none" height={size} viewBox="0 0 24 24" width={size} {...props}>
      <path d={iconPaths[name]} stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </svg>
  );
}
