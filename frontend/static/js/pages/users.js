const dialog = document.querySelector('#user-dialog');
const form = document.querySelector('#user-form');

document.querySelector('#add-user').addEventListener('click', () => {
  form.reset();
  form.action = '/users';
  document.querySelector('#dialog-title').textContent = '新增用户';
  document.querySelector('#submit-user').textContent = '创建用户';
  dialog.showModal();
  form.elements.name.focus();
});

document.querySelectorAll('.edit-user').forEach((button) => {
  button.addEventListener('click', () => {
    form.action = `/users/${encodeURIComponent(button.dataset.id)}/edit`;
    for (const field of ['name', 'email', 'phone', 'role', 'status']) {
      form.elements[field].value = button.dataset[field];
    }
    document.querySelector('#dialog-title').textContent = '编辑用户';
    document.querySelector('#submit-user').textContent = '保存修改';
    dialog.showModal();
    form.elements.name.focus();
  });
});

document.querySelector('#close-dialog').addEventListener('click', () => dialog.close());
document.querySelector('#cancel-dialog').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', (event) => {
  if (event.target === dialog) dialog.close();
});
