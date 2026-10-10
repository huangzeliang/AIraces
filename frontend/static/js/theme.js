const themeToggle = document.querySelector('#theme-toggle');
function applyTheme(theme) {
  const isNight = theme === 'night';
  document.documentElement.dataset.theme = isNight ? 'night' : 'day';
  document.querySelector('meta[name="theme-color"]').content = isNight ? '#080d1a' : '#f6f7ff';
  themeToggle.setAttribute('aria-pressed', String(isNight));
  themeToggle.setAttribute('aria-label', isNight ? '切换到白天模式' : '切换到夜晚模式');
  themeToggle.querySelector('.theme-label').textContent = isNight ? '夜晚' : '白天';
}
applyTheme(localStorage.getItem('northstar-theme') === 'night' ? 'night' : 'day');
themeToggle.addEventListener('click', () => {
  const nextTheme = document.documentElement.dataset.theme === 'night' ? 'day' : 'night';
  localStorage.setItem('northstar-theme', nextTheme);
  applyTheme(nextTheme);
});
