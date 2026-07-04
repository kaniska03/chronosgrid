/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: { 50: "#eef7ff", 100: "#d9edff", 500: "#2e90fa", 600: "#1570cd", 700: "#0f5aa8", 900: "#0b3b6b" },
      },
    },
  },
  plugins: [],
};
