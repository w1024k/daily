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
    order: '/api/tasks/order',
  };

  const CHECK_ICON =
    '<svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" ' +
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8.5l3.2 3.2L13 5"/></svg>';

  // 拖动把手：两列三行的六个小圆点
  const GRIP_ICON =
    '<svg viewBox="0 0 10 16" width="10" height="16" fill="currentColor" aria-hidden="true">' +
    '<circle cx="2.5" cy="2.5" r="1.3"/><circle cx="7.5" cy="2.5" r="1.3"/>' +
    '<circle cx="2.5" cy="8" r="1.3"/><circle cx="7.5" cy="8" r="1.3"/>' +
    '<circle cx="2.5" cy="13.5" r="1.3"/><circle cx="7.5" cy="13.5" r="1.3"/></svg>';

  const TASK_COLORS = [
    { name: 'red', label: '红' },
    { name: 'yellow', label: '黄' },
    { name: 'green', label: '绿' },
  ];
  const COLOR_NAMES = new Set(TASK_COLORS.map((entry) => entry.name));

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
    // 颜色来自服务端（枚举已校验），这里再兜底白名单一次，
    // 确保类名永远不会拼进不可信内容
    const colorName = task.color && COLOR_NAMES.has(task.color) ? task.color : null;

    const li = document.createElement('li');
    li.className =
      'task' +
      (task.completed ? ' task--done' : '') +
      (colorName ? ' task--' + colorName : '') +
      (animate ? '' : ' task--static');
    li.dataset.id = String(task.id);

    // 用 button 而不是 div：Chromium 的触摸命中测试会把不可点击元素上的
    // 触摸重定向到附近最近的可点击元素（touch target adjustment），
    // div 会被跳过去，touch 永远落不到把手上
    const grip = document.createElement('button');
    grip.type = 'button';
    grip.className = 'task__grip';
    grip.setAttribute('tabindex', '-1');
    grip.setAttribute('aria-hidden', 'true');
    grip.innerHTML = GRIP_ICON;
    // 把手上按下立刻可拖；触摸设备上长按卡片正文也行（见拖动排序一节）。
    // move/up/cancel 挂在 document 上，因为拖动中卡片会移动、pointer capture 会失效
    grip.addEventListener('pointerdown', gripPointerDown);
    li.addEventListener('pointerdown', cardPointerDown);

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

    const colors = document.createElement('div');
    colors.className = 'task__colors';
    TASK_COLORS.forEach((entry) => {
      const dot = document.createElement('button');
      dot.type = 'button';
      dot.className = 'task__color' + (task.color === entry.name ? ' is-active' : '');
      dot.dataset.action = 'color';
      dot.dataset.color = entry.name;
      dot.setAttribute('aria-label', entry.label + '色标记');
      dot.setAttribute('aria-pressed', String(task.color === entry.name));
      colors.appendChild(dot);
    });

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

    li.appendChild(grip);
    li.appendChild(check);
    li.appendChild(body);
    li.appendChild(colors);
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

  async function setColor(id, color) {
    const task = findTask(id);
    if (!task) return;

    // 再点一次同色圆点 = 取消标记
    const next = task.color === color ? null : color;
    const element = findElement(id);
    setElementBusy(element, true);
    try {
      const updated = await api(API.tasks + '/' + id, { method: 'PATCH', body: { color: next } });
      const index = state.tasks.findIndex((item) => item.id === id);
      if (index !== -1) state.tasks[index] = updated;
      if (element) element.replaceWith(buildTaskElement(updated, false));
    } catch (error) {
      handleError(error);
      setElementBusy(element, false);
    }
  }

  /* ── 拖动排序 ─────────────────────────────────────────────────────── */

  /*
   * 用 Pointer Events 自己实现，而不是 HTML5 Drag and Drop：
   * 后者在触屏上不可用，而这款应用要在手机上能用。
   * 拖动从卡片左侧的把手开始，不跟列表滚动抢手势。
   */

  const DRAG_THRESHOLD = 8; // 移动超过 8px 才判定为拖动（区分误触）
  /*
   * 触摸设备上按住多久算「长按拖动」。取 300ms 是因为它比浏览器的长按选择
   * （约 500ms）短：手感更利落，也避免浏览器抢先弹出「选择 / 复制」。
   */
  const LONG_PRESS_MS = 300;
  const AUTO_SCROLL_ZONE = 48; // 靠近列表上下边缘时自动滚动的触发区
  const AUTO_SCROLL_STEP = 10;

  // { pointerId, handle, element, started, longPress, timer, startX, startY, originalOrder }
  let dragState = null;
  let autoScrollFrame = null;

  /** 真正进入拖动状态：抬起卡片、禁止选中文字。 */
  function beginDrag(d) {
    if (d.started) return;
    window.clearTimeout(d.timer);
    d.timer = null;
    d.started = true;
    d.originalOrder = Array.from(taskList.children);
    d.element.classList.add('is-dragging');
    document.body.classList.add('is-sorting');
  }

  /** 放弃这次按下（没进入拖动），恢复常态。 */
  function abortPress(d) {
    window.clearTimeout(d.timer);
    d.timer = null;
    d.element.classList.remove('is-pressed');
    if (dragState === d) dragState = null;
  }

  function movedDistance(d, event) {
    return Math.abs(event.clientX - d.startX) + Math.abs(event.clientY - d.startY);
  }

  function gripPointerDown(event) {
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    const element = event.currentTarget.closest('.task');
    if (!element || dragState) return;
    event.preventDefault();
    // 尽量拿到 capture（指针移出窗口时也能收到事件）；DOM 移动会释放它，
    // 后续事件靠 document 级监听兜底，并在每次移动后重建
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch (error) {
      /* capture 失败不影响主流程 */
    }
    element.classList.add('is-pressed');
    dragState = {
      pointerId: event.pointerId,
      handle: event.currentTarget,
      element: element,
      started: false,
      longPress: false,
      timer: null,
      startX: event.clientX,
      startY: event.clientY,
      originalOrder: [],
    };
  }

  /**
   * 触摸设备：长按卡片任意位置也能拖动。
   * 把手只有 14px 宽，手指很难按准，所以整张卡片都是拖动的入口；
   * 手指一动就取消（那说明用户是在滚动列表），长按计时到点才开始拖。
   */
  function cardPointerDown(event) {
    if (event.pointerType !== 'touch' || dragState) return;
    if (event.target.closest('button')) return; // 把手 / 勾选 / 圆点 / 按钮各有各的处理
    const element = event.currentTarget;
    const d = {
      pointerId: event.pointerId,
      handle: null,
      element: element,
      started: false,
      longPress: true,
      timer: null,
      startX: event.clientX,
      startY: event.clientY,
      originalOrder: [],
    };
    element.classList.add('is-pressed');
    d.timer = window.setTimeout(() => beginDrag(d), LONG_PRESS_MS);
    dragState = d;
  }

  function globalPointerMove(event) {
    const d = dragState;
    if (!d || d.pointerId !== event.pointerId) return;

    if (!d.started) {
      if (movedDistance(d, event) < DRAG_THRESHOLD) return;
      // 长按还没到点手指就动了：用户在滚动列表，让给他
      if (d.longPress) {
        abortPress(d);
        return;
      }
      beginDrag(d);
    }

    event.preventDefault();
    autoScroll(event.clientY);

    // 找到指针下方第一张「中线在其下方」的兄弟卡片，插到它前面
    const siblings = Array.from(taskList.children).filter((li) => li !== d.element);
    let reference = null;
    for (const sibling of siblings) {
      const rect = sibling.getBoundingClientRect();
      if (event.clientY < rect.top + rect.height / 2) {
        reference = sibling;
        break;
      }
    }
    if (reference === d.element.nextElementSibling) return; // 位置没变
    moveTaskElement(d.element, reference);
  }

  function globalPointerUp(event) {
    const d = dragState;
    if (!d || d.pointerId !== event.pointerId) return;
    dragState = null;
    window.clearTimeout(d.timer);
    stopAutoScroll();
    document.body.classList.remove('is-sorting');
    d.element.classList.remove('is-pressed');
    if (!d.started) return;

    d.element.classList.remove('is-dragging');

    const order = Array.from(taskList.children).map((li) => Number(li.dataset.id));
    const original = d.originalOrder.map((li) => Number(li.dataset.id));
    const changed =
      order.length !== original.length || order.some((id, index) => id !== original[index]);
    if (!changed) return;

    applyOrder(order); // 本地立即生效
    persistOrder(order); // 异步提交给服务端，失败时回滚
  }

  function globalPointerCancel(event) {
    const d = dragState;
    if (!d || d.pointerId !== event.pointerId) return;
    cancelDrag();
  }

  function cancelDrag() {
    const d = dragState;
    dragState = null;
    if (d) {
      window.clearTimeout(d.timer);
      d.element.classList.remove('is-pressed');
    }
    stopAutoScroll();
    document.body.classList.remove('is-sorting');
    if (!d || !d.started) return;
    d.element.classList.remove('is-dragging');
    // 触摸被系统打断 / 按了 Esc：恢复拖动前的顺序
    taskList.textContent = '';
    d.originalOrder.forEach((li) => taskList.appendChild(li));
  }

  /** 把被拖卡片插到 reference 前，其余卡片用 FLIP 动画平滑让位。 */
  function moveTaskElement(element, reference) {
    const items = Array.from(taskList.children);
    const tops = new Map();
    items.forEach((li) => tops.set(li, li.getBoundingClientRect().top));

    if (reference === null) taskList.appendChild(element);
    else taskList.insertBefore(element, reference);

    // 卡片移动会把它从文档树摘下再插入，pointer capture 随之释放，
    // 这里重建，让指针移出窗口等边缘情况仍能收到事件
    // （长按卡片正文开始的拖动没有把手可捕获，靠 document 级监听就够）
    if (dragState && dragState.handle) {
      try {
        dragState.handle.setPointerCapture(dragState.pointerId);
      } catch (error) {
        /* 忽略 */
      }
    }

    items.forEach((li) => {
      const delta = tops.get(li) - li.getBoundingClientRect().top;
      if (delta === 0) return;
      // 入场动画 fill-mode: both 会锁死 transform，先解除再用 transform 过渡
      li.style.animation = 'none';
      li.style.transition = 'none';
      li.style.transform = 'translateY(' + delta + 'px)';
      li.offsetHeight; // 强制 reflow，让浏览器记录起始位置
      li.style.transition = '';
      li.style.transform = '';
    });
  }

  function applyOrder(order) {
    const byId = new Map(state.tasks.map((task) => [task.id, task]));
    const next = order.map((id) => byId.get(id)).filter(Boolean);
    if (next.length !== state.tasks.length) return;
    state.tasks = next;
    updateMeta();
  }

  async function persistOrder(order) {
    try {
      await api(API.order, { method: 'PUT', body: { ids: order } });
    } catch (error) {
      handleError(error);
      loadTasks(); // 以服务端顺序为准，回滚本地改动
    }
  }

  function autoScroll(clientY) {
    const rect = board.getBoundingClientRect();
    let step = 0;
    if (clientY < rect.top + AUTO_SCROLL_ZONE) step = -AUTO_SCROLL_STEP;
    else if (clientY > rect.bottom - AUTO_SCROLL_ZONE) step = AUTO_SCROLL_STEP;
    if (step === 0) {
      stopAutoScroll();
      return;
    }

    function tick() {
      const before = board.scrollTop;
      board.scrollTop += step;
      if (board.scrollTop === before) {
        stopAutoScroll(); // 到顶/到底了
        return;
      }
      autoScrollFrame = requestAnimationFrame(tick);
    }
    if (autoScrollFrame === null) autoScrollFrame = requestAnimationFrame(tick);
  }

  function stopAutoScroll() {
    if (autoScrollFrame !== null) {
      cancelAnimationFrame(autoScrollFrame);
      autoScrollFrame = null;
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
    else if (button.dataset.action === 'color') setColor(id, button.dataset.color);
    else if (button.dataset.action === 'delete') deleteTask(id);
  });

  // 拖动的 move/up/cancel 挂在 document 上：拖动中卡片会移动，
  // capture 可能失效，而 pointer 事件会冒泡到 document，这里必然收到
  document.addEventListener('pointermove', globalPointerMove);
  document.addEventListener('pointerup', globalPointerUp);
  document.addEventListener('pointercancel', globalPointerCancel);

  /*
   * 长按拖动期间要挡住列表滚动：只有非 passive 的 touchmove 才能 preventDefault，
   * 而且必须在手势开始前就注册好 —— 浏览器据此决定「要不要等这段 JS」，
   * 临时加的监听器已经晚了，preventDefault 会被忽略。
   */
  document.addEventListener(
    'touchmove',
    (event) => {
      if (dragState && dragState.started) event.preventDefault();
    },
    { passive: false }
  );

  // 安卓 Chrome 长按会弹上下文菜单，拖动时得拦下来，否则拖动被打断
  document.addEventListener('contextmenu', (event) => {
    if (dragState) event.preventDefault();
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && dragState && dragState.started) {
      cancelDrag();
      return;
    }
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
