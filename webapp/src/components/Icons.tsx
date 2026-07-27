import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function IconBase({ children, ...props }: IconProps): React.ReactElement {
  return (
    <svg
      aria-hidden="true"
      fill="none"
      height="24"
      viewBox="0 0 24 24"
      width="24"
      {...props}
    >
      {children}
    </svg>
  );
}

export function CameraIcon(props: IconProps): React.ReactElement {
  return (
    <IconBase {...props}>
      <path
        d="M4.5 7.75A2.25 2.25 0 0 1 6.75 5.5h7.5a2.25 2.25 0 0 1 2.25 2.25v8.5a2.25 2.25 0 0 1-2.25 2.25h-7.5a2.25 2.25 0 0 1-2.25-2.25v-8.5Z"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <path
        d="m16.5 10.1 2.84-1.65a.77.77 0 0 1 1.16.67v5.76a.77.77 0 0 1-1.16.67L16.5 13.9"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.7"
      />
    </IconBase>
  );
}

export function ClimateIcon(props: IconProps): React.ReactElement {
  return (
    <IconBase {...props}>
      <path
        d="M9 4.75a3 3 0 1 1 6 0v7.18a5 5 0 1 1-6 0V4.75Z"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <path
        d="M12 7v7.25m0 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z"
        stroke="currentColor"
        strokeLinecap="round"
        strokeWidth="1.7"
      />
    </IconBase>
  );
}

export function PlayIcon(props: IconProps): React.ReactElement {
  return (
    <IconBase {...props}>
      <path
        d="m9 7.2 7.1 4.22a.67.67 0 0 1 0 1.16L9 16.8a.67.67 0 0 1-1-.58V7.78a.67.67 0 0 1 1-.58Z"
        fill="currentColor"
      />
    </IconBase>
  );
}

export function StopIcon(props: IconProps): React.ReactElement {
  return (
    <IconBase {...props}>
      <rect fill="currentColor" height="10" rx="1.5" width="10" x="7" y="7" />
    </IconBase>
  );
}

export function RecordIcon(props: IconProps): React.ReactElement {
  return (
    <IconBase {...props}>
      <circle cx="12" cy="12" fill="currentColor" r="5.5" />
      <circle cx="12" cy="12" r="8.5" stroke="currentColor" strokeWidth="1.5" />
    </IconBase>
  );
}

export function DownloadIcon(props: IconProps): React.ReactElement {
  return (
    <IconBase {...props}>
      <path
        d="M12 4v10m0 0 3.5-3.5M12 14l-3.5-3.5M5 16.5v1.25A2.25 2.25 0 0 0 7.25 20h9.5A2.25 2.25 0 0 0 19 17.75V16.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </IconBase>
  );
}

export function RefreshIcon(props: IconProps): React.ReactElement {
  return (
    <IconBase {...props}>
      <path
        d="M19 8.5A7.5 7.5 0 1 0 19.22 15M19 8.5V4m0 4.5h-4.5"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </IconBase>
  );
}

export function ChevronIcon(props: IconProps): React.ReactElement {
  return (
    <IconBase {...props}>
      <path
        d="m9 6 6 6-6 6"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.8"
      />
    </IconBase>
  );
}

export function ShieldIcon(props: IconProps): React.ReactElement {
  return (
    <IconBase {...props}>
      <path
        d="M12 3.5 19 6v5.25c0 4.38-2.74 7.63-7 9.25-4.26-1.62-7-4.87-7-9.25V6l7-2.5Z"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.7"
      />
      <path
        d="m9 12 2 2 4-4"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.7"
      />
    </IconBase>
  );
}
