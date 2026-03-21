import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        base: "#0A0E12",
        surface: "#161B22",
        "surface-alt": "#1C2128",
        border: "#21262D",
        bullish: "#00FFC2",
        "bullish-muted": "rgba(0, 255, 194, 0.2)",
        bearish: "#FF4B2B",
        "bearish-muted": "rgba(255, 75, 43, 0.2)",
        neutral: "#8B949E",
        "text-primary": "#E6EDF3",
        "text-secondary": "#B1BAC4",
        "text-muted": "#7D8590",
        "accent-blue": "#58A6FF",
        "volume-spike": "#3B82F6",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["Roboto Mono", "monospace"],
      },
      borderRadius: {
        card: "8px",
      },
      spacing: {
        "card-padding": "16px",
        "card-padding-lg": "20px",
        "module-gap": "12px",
        "module-gap-lg": "16px",
      },
    },
  },
  plugins: [],
};
export default config;
