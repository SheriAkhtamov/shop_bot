/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/templates/**/*.html", "./app/web/static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        primary: '#6B3F1E',
        'primary-hover': '#543116',
        bg: '#F2E8DC',
        text: '#3E2310',
        'text-secondary': '#8C735F',
        coffee: {
            50: '#FCF9F5',
            100: '#F2E8DC',
            200: '#E0CCB7',
            800: '#3E2310',
            900: '#2A180B',
        }
      },
      fontFamily: {
        sans: ['Manrope', 'sans-serif'],
      },
      boxShadow: {
        'coffee': '0 10px 15px -3px rgba(107, 63, 30, 0.1), 0 4px 6px -2px rgba(107, 63, 30, 0.05)',
        'glass': '0 8px 32px 0 rgba(107, 63, 30, 0.1)',
      }
    },
  },
  plugins: [],
}
