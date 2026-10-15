const dialog = document.querySelector('#user-dialog');
const form = document.querySelector('#user-form');
const passwordField = document.querySelector('#password-field');
const selfHint = document.querySelector('#self-hint');
const resetDialog = document.querySelector('#reset-dialog');
const resetForm = document.querySelector('#reset-form');

document.querySelector('#add-user').addEventListener('click', () => {
  form.reset();
  form.action = '/users';
  form.elements.role.disabled = false;
  form.elements.status.disabled = false;
  passwordField.hidden = false;
  form.elements.password.required = true;
  selfHint.hidden = true;
  document.querySelector('#dialog-title').textContent = '新增用户';
  document.querySelector('#submit-user').textContent = '创建用户';
  dialog.showModal();
  form.elements.name.focus();
});

document.querySelectorAll('.edit-user').forEach((button) => {
  button.addEventListener('click', () => {
    form.reset();
    form.action = `/users/${encodeURIComponent(button.dataset.id)}/edit`;
    for (const field of ['name', 'email', 'phone', 'role', 'status']) {
      form.elements[field].value = button.dataset[field];
    }
    // 编辑资料不改密码：隐藏密码字段；当前登录账号不可改角色与状态
    passwordField.hidden = true;
    form.elements.password.required = false;
    const isSelf = button.dataset.self === '1';
    form.elements.role.disabled = isSelf;
    form.elements.status.disabled = isSelf;
    selfHint.hidden = !isSelf;
    document.querySelector('#dialog-title').textContent = '编辑用户';
    document.querySelector('#submit-user').textContent = '保存修改';
    dialog.showModal();
    form.elements.name.focus();
  });
});

// 禁用的下拉在提交前恢复（值保持原样），后端仍会做归属与角色校验
form.addEventListener('submit', () => {
  form.querySelectorAll('select[disabled]').forEach((select) => { select.disabled = false; });
});

document.querySelector('#close-dialog').addEventListener('click', () => dialog.close());
document.querySelector('#cancel-dialog').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', (event) => {
  if (event.target === dialog) dialog.close();
});

document.querySelectorAll('.reset-password').forEach((button) => {
  button.addEventListener('click', () => {
    resetForm.reset();
    resetForm.action = `/users/${encodeURIComponent(button.dataset.id)}/reset-password`;
    document.querySelector('#reset-subtitle').textContent = `为「${button.dataset.name}」设置新的登录密码`;
    resetDialog.showModal();
    resetForm.elements.password.focus();
  });
});

document.querySelector('#close-reset').addEventListener('click', () => resetDialog.close());
document.querySelector('#cancel-reset').addEventListener('click', () => resetDialog.close());
resetDialog.addEventListener('click', (event) => {
  if (event.target === resetDialog) resetDialog.close();
});
