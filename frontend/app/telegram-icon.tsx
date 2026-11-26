type TelegramIconProps = {
  size?: number;
  className?: string;
};

export default function TelegramIcon({
  size = 20,
  className = "",
}: TelegramIconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role="img"
      aria-label="Telegram"
      className={`inline-block shrink-0 ${className}`}
    >
      <circle cx="12" cy="12" r="12" fill="#229ED9" />
      <path
        d="M18.95 6.15 16.7 17.1c-.17.77-.62.96-1.25.6l-3.45-2.54-1.66 1.6c-.18.18-.33.33-.68.33l.24-3.5 6.37-5.76c.28-.24-.06-.38-.43-.14L8.07 12.8l-3.4-1.06c-.74-.23-.75-.74.16-1.1l13.3-5.13c.62-.23 1.16.15.82.64Z"
        fill="white"
      />
    </svg>
  );
}
