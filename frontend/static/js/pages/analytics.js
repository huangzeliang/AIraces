document.querySelectorAll('.range-tabs button').forEach((button) => {
  button.addEventListener('click', () => {
    document.querySelectorAll('.range-tabs button').forEach((item) => item.classList.toggle('active', item === button));
  });
});
