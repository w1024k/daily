/* ==========================================================================
   计划管理 —— 前端逻辑
   纯原生 JS，无框架、无构建步骤。所有用户内容都用 textContent 写入，
   不使用 innerHTML 拼接，天然免疫 XSS。
   ========================================================================== */

'use strict';

(function () {
  const API = {
    me: '/api/auth/me',
    login: '/api/auth/login',
    register: '/api/auth/register',
    logout: '/api/auth/logout',
    tasks: '/api/tasks',
  };

  const CHECK_ICON =
    '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" ' +
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.2 3.2L13 5"/></svg>';

  const state = {
    user: null,
    tasks: [],
    authMode: 'login', // 'login' | 'register'
  };

  const $ = (id) => document.getElementById(id);

  const authView = $('auth-view');
  const authForm = $('auth-form');
  const authTitle = $('auth-title');
  const authSubtitle = $('auth-subtitle');
  const authUsername = $('auth-username');
  const authPassword = $('auth-password');
  const authError = $('auth-error');
  const authSubmit = $('auth-submit');
  const authSwitch = $('auth-switch');
  const authSwitchHint = $('auth-switch-hint');

  const appView = $('app-view');
  const currentUser = $('current-user');
  const logoutButton = $('logout');
  const board = $('board');
  const boardMeta = $('board-meta');
  const taskList = $('task-list');
  const emptyState = $('empty-state');
  const taskForm = $('task-form');
  const taskInput = $('task-input');
  const taskSubmit = $('task-submit');

  const modal = $('modal');
  const modalTitle = $('modal-title');
  const modalText = $('modal-text');
  const modalConfirm = $('modal-confirm');
  const modalCancel = $('modal-cancel');
  const toastEl = $('toast');

  /* ── 网络 ─────────────────────────────────────────────────────────── */

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }

  function extractMessage(payload, status) {
    if (payload && typeof payload.detail === 'string') return payload.detail;
    // FastAPI 校验失败时 detail 是数组
    if (payload && Array.isArray(payload.detail) && payload.detail.length > 0) {
      const first = payload.detail[0];
      if (first && typeof first.msg === 'string') {
        return first.msg.replace(/^Value error,\s*/, '');
      }
    }
    if (status === 401) return '登录已过期，请重新登录';
    if (status === 404) return '请求的内容不存在';
    return '请求失败（HTTP ' + status + '）';
  }

  async function api(path, options) {
    const opts = options || {};
    const init = {
      method: opts.method || 'GET',
      credentials: 'same-origin',
      headers: {},
    };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }

    let response;
    try {
      response = await fetch(path, init);
    } catch (error) {
      throw new ApiError('网络连接失败，请确认服务是否正常', 0);
    }

    if (response.status === 204) return null;

    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        payload = null;
      }
    }

    if (!response.ok) throw new ApiError(extractMessage(payload, response.status), response.status);
    return payload;
  }

  /* ── 视图切换 ─────────────────────────────────────────────────────── */

  function showAuth() {
    state.user = null;
    state.tasks = [];
    appView.hidden = true;
    authView.hidden = false;
    authPassword.value = '';
    authUsername.focus();
  }

  function enterApp(user) {
    state.user = user;
    currentUser.textContent = user.username;
    authView.hidden = true;
    appView.hidden = false;
    loadTasks();
    taskInput.focus();
  }

  /* ── 登录 / 注册 ──────────────────────────────────────────────────── */

  function setAuthMode(mode) {
    state.authMode = mode;
    const isLogin = mode === 'login';
    authTitle.textContent = isLogin ? '欢迎回来' : '创建账号';
    authSubtitle.textContent = isLogin ? '登录后开始管理你的计划' : '注册后即可拥有自己的计划清单';
    authSubmit.textContent = isLogin ? '登录' : '注册';
    authSwitchHint.textContent = isLogin ? '还没有账号？' : '已经有账号了？';
    authSwitch.textContent = isLogin ? '立即注册' : '去登录';
    authPassword.setAttribute('autocomplete', isLogin ? 'current-password' : 'new-password');
    hideAuthError();
  }

  function showAuthError(message) {
    authError.textContent = message;
    authError.hidden = false;
  }

  function hideAuthError() {
    authError.hidden = true;
    authError.textContent = '';
  }

  function validateAuth(username, password) {
    if (username.length < 2) return '用户名至少 2 个字符';
    if (username.length > 32) return '用户名最长 32 个字符';
    if (/\s/.test(username)) return '用户名不能包含空格';
    if (password.length < 6) return '密码至少 6 位';
    if (password.length > 128) return '密码最长 128 位';
    return null;
  }

  function setBusy(button, busy, busyLabel) {
    if (busy) {
      button.dataset.label = button.textContent;
      if (busyLabel) button.textContent = busyLabel;
      button.disabled = true;
    } else {
      if (button.dataset.label) button.textContent = button.dataset.label;
      button.disabled = false;
    }
  }

  async function submitAuth(event) {
    event.preventDefault();
    const username = authUsername.value.trim();
    const password = authPassword.value;

    const problem = validateAuth(username, password);
    if (problem) {
      showAuthError(problem);
      return;
    }
    hideAuthError();

    const isLogin = state.authMode === 'login';
    setBusy(authSubmit, true, isLogin ? '登录中…' : '注册中…');
    try {
      const user = await api(isLogin ? API.login : API.register, {
        method: 'POST',
        body: { username: username, password: password },
      });
      authForm.reset();
      enterApp(user);
    } catch (error) {
      showAuthError(error.message);
    } finally {
      setBusy(authSubmit, false);
    }
  }

  async function logout() {
    try {
      await api(API.logout, { method: 'POST' });
    } catch (error) {
      /* 退出失败也要回到登录页 */
    }
    showAuth();
    setAuthMode('login');
  }

  /* ── 任务列表 ─────────────────────────────────────────────────────── */

  function pad2(value) {
    return String(value).padStart(2, '0');
  }

  /** UTC ISO 字符串 -> 本地时间 "MM-DD HH:mm"（跨年时补上年份） */
  function formatTime(iso) {
    if (!iso) return '';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return '';

    const now = new Date();
    const stamp = pad2(date.getMonth() + 1) + '-' + pad2(date.getDate()) +
      ' ' + pad2(date.getHours()) + ':' + pad2(date.getMinutes());
    return date.getFullYear() === now.getFullYear() ? stamp : date.getFullYear() + '-' + stamp;
  }

  function truncate(text, limit) {
    return text.length > limit ? text.slice(0, limit) + '…' : text;
  }

  function buildTaskElement(task, animate) {
    const li = document.createElement('li');
    li.className = 'task' + (task.completed ? ' task--done' : '') + (animate ? '' : ' task--static');
    li.dataset.id = String(task.id);

    const check = document.createElement('button');
    check.type = 'button';
    check.className = 'task__check';
    check.dataset.action = 'toggle';
    check.setAttribute('aria-label', task.completed ? '取消完成' : '标记完成');
    check.setAttribute('aria-pressed', String(task.completed));
    check.innerHTML = CHECK_ICON;

    const body = document.createElement('div');
    body.className = 'task__body';

    const content = document.createElement('p');
    content.className = 'task__content';
    content.textContent = task.content;

    const meta = document.createElement('p');
    meta.className = 'task__meta';
    meta.appendChild(document.createTextNode('创建于 ' + formatTime(task.created_at)));
    if (task.completed && task.completed_at) {
      const sep = document.createElement('span');
      sep.className = 'task__meta-sep';
      sep.textContent = '·';
      meta.appendChild(sep);
      meta.appendChild(document.createTextNode('完成于 ' + formatTime(task.completed_at)));
    }

    body.appendChild(content);
    body.appendChild(meta);

    const actions = document.createElement('div');
    actions.className = 'task__actions';

    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'task__action';
    toggle.dataset.action = 'toggle';
    toggle.textContent = task.completed ? '取消完成' : '完成';

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'task__action task__action--danger';
    remove.dataset.action = 'delete';
    remove.textContent = '删除';

    actions.appendChild(toggle);
    actions.appendChild(remove);

    li.appendChild(check);
    li.appendChild(body);
    li.appendChild(actions);
    return li;
  }

  function updateMeta() {
    const total = state.tasks.length;
    const done = state.tasks.filter((task) => task.completed).length;
    boardMeta.textContent = total === 0 ? '' : '共 ' + total + ' 项 · 已完成 ' + done + ' 项';
    emptyState.hidden = total !== 0;
  }

  function renderTasks() {
    taskList.textContent = '';
    const fragment = document.createDocumentFragment();
    state.tasks.forEach((task) => fragment.appendChild(buildTaskElement(task, true)));
    taskList.appendChild(fragment);
    updateMeta();
  }

  function findTask(id) {
    return state.tasks.find((task) => task.id === id) || null;
  }

  function findElement(id) {
    return taskList.querySelector('.task[data-id="' + id + '"]');
  }

  function setElementBusy(element, busy) {
    if (!element) return;
    element.querySelectorAll('button').forEach((button) => {
      button.disabled = busy;
    });
  }

  async function loadTasks() {
    try {
      const payload = await api(API.tasks);
      state.tasks = payload && Array.isArray(payload.tasks) ? payload.tasks : [];
      renderTasks();
      // 最新的任务在最上面，停在顶部
      board.scrollTop = 0;
    } catch (error) {
      handleError(error);
    }
  }

  async function addTask(event) {
    event.preventDefault();
    const content = taskInput.value.trim();
    if (!content) return;

    setBusy(taskSubmit, true, '添加中…');
    try {
      const task = await api(API.tasks, { method: 'POST', body: { content: content } });
      taskInput.value = '';
      // 新任务插到列表最前面，并滚回顶部让它可见
      state.tasks.unshift(task);
      taskList.prepend(buildTaskElement(task, true));
      updateMeta();
      board.scrollTop = 0;
    } catch (error) {
      handleError(error);
    } finally {
      setBusy(taskSubmit, false);
      taskInput.focus();
    }
  }

  async function toggleTask(id) {
    const task = findTask(id);
    if (!task) return;

    const element = findElement(id);
    setElementBusy(element, true);
    try {
      const updated = await api(API.tasks + '/' + id, {
        method: 'PATCH',
        body: { completed: !task.completed },
      });
      const index = state.tasks.findIndex((item) => item.id === id);
      if (index !== -1) state.tasks[index] = updated;
      if (element) element.replaceWith(buildTaskElement(updated, false));
      updateMeta();
    } catch (error) {
      handleError(error);
      setElementBusy(element, false);
    }
  }

  async function deleteTask(id) {
    const task = findTask(id);
    if (!task) return;

    const confirmed = await confirmDialog({
      title: '确认删除',
      text: '确定要删除「' + truncate(task.content, 40) + '」吗？删除后将从列表中移除。',
      confirmText: '删除',
    });
    if (!confirmed) return;

    const element = findElement(id);
    try {
      await api(API.tasks + '/' + id, { method: 'DELETE' });
      state.tasks = state.tasks.filter((item) => item.id !== id);
      if (element) {
        element.classList.add('is-removing');
        window.setTimeout(() => element.remove(), 200);
      }
      updateMeta();
    } catch (error) {
      handleError(error);
    }
  }

  /* ── 确认弹窗 ─────────────────────────────────────────────────────── */

  function confirmDialog(options) {
    const config = options || {};
    return new Promise((resolve) => {
      const opener = document.activeElement;

      modalTitle.textContent = config.title || '请确认';
      modalText.textContent = config.text || '';
      modalConfirm.textContent = config.confirmText || '确定';
      modal.hidden = false;
      modalConfirm.focus();

      function finish(result) {
        modal.hidden = true;
        modalConfirm.removeEventListener('click', onConfirm);
        modalCancel.removeEventListener('click', onCancel);
        modal.removeEventListener('click', onBackdrop);
        document.removeEventListener('keydown', onKey);
        if (opener && document.contains(opener) && typeof opener.focus === 'function') {
          opener.focus();
        }
        resolve(result);
      }

      function onConfirm() { finish(true); }
      function onCancel() { finish(false); }
      function onBackdrop(event) {
        if (event.target.dataset && event.target.dataset.dismiss === 'modal') finish(false);
      }
      function onKey(event) {
        if (event.key === 'Escape') finish(false);
      }

      modalConfirm.addEventListener('click', onConfirm);
      modalCancel.addEventListener('click', onCancel);
      modal.addEventListener('click', onBackdrop);
      document.addEventListener('keydown', onKey);
    });
  }

  /* ── 轻提示 ───────────────────────────────────────────────────────── */

  let toastTimer = null;

  function toast(message) {
    toastEl.textContent = message;
    toastEl.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      toastEl.hidden = true;
    }, 2800);
  }

  function handleError(error) {
    if (error && error.status === 401) {
      showAuth();
      setAuthMode('login');
      toast('登录已过期，请重新登录');
      return;
    }
    toast(error && error.message ? error.message : '操作失败，请稍后重试');
  }

  /* ── 事件绑定 ─────────────────────────────────────────────────────── */

  authForm.addEventListener('submit', submitAuth);
  authSwitch.addEventListener('click', () => {
    setAuthMode(state.authMode === 'login' ? 'register' : 'login');
    authUsername.focus();
  });

  logoutButton.addEventListener('click', logout);
  taskForm.addEventListener('submit', addTask);

  taskList.addEventListener('click', (event) => {
    const button = event.target.closest('[data-action]');
    if (!button) return;
    const element = button.closest('.task');
    if (!element) return;
    const id = Number(element.dataset.id);
    if (button.dataset.action === 'toggle') toggleTask(id);
    else if (button.dataset.action === 'delete') deleteTask(id);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === '/' && document.activeElement !== taskInput && !appView.hidden) {
      event.preventDefault();
      taskInput.focus();
    }
  });

  /* ── 启动 ─────────────────────────────────────────────────────────── */

  async function init() {
    setAuthMode('login');
    try {
      const user = await api(API.me);
      enterApp(user);
    } catch (error) {
      showAuth();
      if (error.status !== 401) toast(error.message);
    }
  }

  init();
})();
