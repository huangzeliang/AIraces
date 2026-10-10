document.querySelectorAll('.switch').forEach((switchButton) => {
  switchButton.addEventListener('click', () => {
    const enabled = !switchButton.classList.contains('on');
    switchButton.classList.toggle('on', enabled);
    switchButton.setAttribute('aria-pressed', String(enabled));
  });
});
