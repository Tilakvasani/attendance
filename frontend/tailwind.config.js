/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,jsx}", "./components/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        surface: "#1a1f2e",
        border:  "#2a2f45",
      },
    },
  },
  plugins: [],
};
