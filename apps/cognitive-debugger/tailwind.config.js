/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Helvetica Neue"', "Helvetica", "Arial", "sans-serif"],
      },
      colors: {
        ink: "#1f2b20",
        stone: "#d9d0bf",
        mist: "#eef4ea",
        ivory: "#fffaf0",
        muted: "#667167",
        warm: "#f5f3ee",
        accent: "#705d2f",
        success: "#355b3d",
        warning: "#8a6b20",
        danger: "#8f3d33",
      },
      boxShadow: {
        panel: "0 18px 48px rgba(18, 24, 18, 0.08)",
      },
      borderRadius: {
        shell: "1.4rem",
      },
    },
  },
  plugins: [],
};
