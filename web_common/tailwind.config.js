/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./web_common/**/*.{html,js}",
    "../auth/**/*.{html,js}",
    "../batch/**/*.{html,js}",
    "../ci/**/*.{html,js}",
    "../monitoring/**/*.{html,js}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
