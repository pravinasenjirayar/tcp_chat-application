const state = {
  sessionId: "",
  eventSource: null,
  rooms: [],
  users: [],
  messages: [],
  currentRoom: "General",
  currentUser: null,
};

const landingPage = document.getElementById("landing-page");
const authPage = document.getElementById("auth-page");
const appShell = document.getElementById("app-shell");
const roomList = document.getElementById("room-list");
const onlineUsers = document.getElementById("online-users");
const messagesBox = document.getElementById("messages");
const roomTitle = document.getElementById("room-title");
const roomSubtitle = document.getElementById("room-subtitle");
const searchInput = document.getElementById("search-input");
const searchResults = document.getElementById("search-results");
const toastContainer = document.getElementById("toast-container");
const settingsModal = document.getElementById("settings-modal");
const profileModal = document.getElementById("profile-modal");
const settingsUsername = document.getElementById("settings-username");
const themeSelect = document.getElementById("theme-select");

function showView(viewName) {
  const views = {
    landing: landingPage,
    auth: authPage,
    app: appShell,
  };

  Object.entries(views).forEach(([name, element]) => {
    element.classList.toggle("hidden-panel", name !== viewName);
  });
}

function showToast(message, type = "info") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  toastContainer.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 2600);
}

function setTheme(theme) {
  const isLight = theme === "light";
  document.body.classList.toggle("light-mode", isLight);
  localStorage.setItem("tcpChatTheme", theme);
  themeSelect.value = theme;
}

function applySavedTheme() {
  const theme = localStorage.getItem("tcpChatTheme") || "dark";
  setTheme(theme);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const text = await response.text();
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = null;
    }
    throw new Error(parsed?.message || response.statusText || "Request failed.");
  }

  return response.json();
}

async function loginUser(event) {
  event.preventDefault();
  const usernameInput = document.getElementById("username");
  const displayNameInput = document.getElementById("display-name");

  const username = (usernameInput.value || "").trim();
  const displayName = (displayNameInput.value || "").trim();

  if (!username) {
    showToast("Username is required.", "error");
    return;
  }

  try {
    const result = await fetchJson("/api/login", {
      method: "POST",
      body: JSON.stringify({ username, displayName }),
    });

    if (!result.success) {
      showToast(result.message || "Unable to join chat.", "error");
      return;
    }

    state.sessionId = result.sessionId;
    connectEventSource();
    applyState(result.state);
    showView("app");
    showToast("Connected successfully.", "success");
    searchInput.value = "";
    searchResults.classList.add("hidden");
    document.getElementById("message-input").focus();
  } catch (error) {
    showToast(error.message || "Server unavailable.", "error");
  }
}

function connectEventSource() {
  if (state.eventSource) {
    state.eventSource.close();
  }

  if (!state.sessionId) {
    return;
  }

  updateConnectionStatus("● Connecting");
  state.eventSource = new EventSource(`/events?session=${encodeURIComponent(state.sessionId)}`);
  state.eventSource.onopen = () => updateConnectionStatus("● Connected");
  state.eventSource.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload.type === "state") {
        applyState(payload.payload);
      }
    } catch (error) {
      console.error("Event stream parse error:", error);
    }
  };

  state.eventSource.onerror = () => {
    updateConnectionStatus("● Reconnecting");
  };
}

function applyState(snapshot) {
  const safeSnapshot = snapshot || {};
  const roomData = safeSnapshot.rooms || [];
  const userList = safeSnapshot.users || [];
  const roomMessages = safeSnapshot.messages || [];
  const currentUser = safeSnapshot.currentUser || state.currentUser || { username: "Guest", displayName: "Guest" };

  state.rooms = roomData;
  state.users = userList;
  state.messages = roomMessages;
  state.currentRoom = safeSnapshot.currentRoom || state.currentRoom || "General";
  state.currentUser = currentUser;

  const roomDescription = roomData.find((room) => room.name === state.currentRoom)?.description || "General discussion room";
  roomTitle.textContent = `# ${state.currentRoom}`;
  roomSubtitle.textContent = roomDescription;

  const sidebarUser = document.getElementById("sidebar-username");
  const userAvatar = document.getElementById("user-avatar");
  const profileAvatar = document.getElementById("profile-avatar");
  const profileName = document.getElementById("profile-name");
  const profileStatus = document.getElementById("profile-status");
  const profileSessionTime = document.getElementById("profile-session-time");
  const displayName = currentUser.displayName || currentUser.username || "Guest";
  const shortInitial = (displayName || "U").charAt(0).toUpperCase();

  sidebarUser.textContent = displayName;
  userAvatar.textContent = shortInitial;
  profileAvatar.textContent = shortInitial;
  profileName.textContent = displayName;
  profileStatus.textContent = "Online";
  profileSessionTime.textContent = "Joined just now";
  settingsUsername.value = displayName;

  renderRooms();
  renderUsers();
  renderMessages();
}

function renderRooms() {
  roomList.innerHTML = "";

  state.rooms.forEach((room) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `room-item ${room.name === state.currentRoom ? "active" : ""}`;
    btn.dataset.room = room.name;
    const name = document.createElement("span");
    name.className = "room-name";
    name.textContent = `# ${room.name}`;
    const count = document.createElement("span");
    count.className = "room-count";
    count.textContent = String(room.memberCount || 0);
    btn.append(name, count);
    roomList.appendChild(btn);
  });
}

function renderUsers() {
  onlineUsers.innerHTML = "";

  if (!state.users.length) {
    onlineUsers.innerHTML = '<div class="user-item"><span class="user-status">No users online</span></div>';
    return;
  }

  state.users.forEach((user) => {
    const item = document.createElement("div");
    item.className = "user-item";
    const indicator = document.createElement("span");
    indicator.className = "user-indicator";
    const name = document.createElement("span");
    name.textContent = user.displayName || user.username;
    item.append(indicator, name);
    onlineUsers.appendChild(item);
  });
}

function renderMessages() {
  messagesBox.innerHTML = "";

  if (!state.messages.length) {
    const empty = document.createElement("div");
    empty.className = "message-bubble system";
    empty.textContent = "No messages in this room yet. Start the conversation.";
    messagesBox.appendChild(empty);
    return;
  }

  state.messages.forEach((message) => {
    const row = document.createElement("div");
    const isSelf = message.username === state.currentUser?.username;
    row.className = `message-row ${isSelf ? "self" : "other"}`;

    const bubble = document.createElement("div");
    bubble.className = `message-bubble ${message.system ? "system" : ""}`;

    if (message.system) {
      bubble.textContent = message.text;
    } else {
      const header = document.createElement("div");
      header.className = "message-header";
      const sender = document.createElement("strong");
      sender.textContent = message.displayName || message.username;
      const timestamp = document.createElement("span");
      timestamp.textContent = message.timestamp || "";
      header.append(sender, timestamp);
      const body = document.createElement("div");
      body.className = "message-body";
      body.textContent = message.text;
      bubble.append(header, body);
    }

    row.appendChild(bubble);
    messagesBox.appendChild(row);
  });

  requestAnimationFrame(() => {
    messagesBox.scrollTop = messagesBox.scrollHeight;
  });
}

function updateConnectionStatus(status) {
  const chip = document.getElementById("connection-status");
  if (chip) {
    chip.textContent = status;
  }
}

async function handleRoomSwitch(roomName) {
  if (!state.sessionId || !roomName) {
    return;
  }

  try {
    const result = await fetchJson("/api/switch-room", {
      method: "POST",
      body: JSON.stringify({ sessionId: state.sessionId, room: roomName }),
    });

    if (!result.success) {
      showToast(result.message || "Unable to switch room.", "error");
      return;
    }

    applyState(result.state);
    showToast(`Joined #${roomName}`, "info");
  } catch (error) {
    showToast(error.message || "Unable to switch room.", "error");
  }
}

async function sendMessage() {
  const input = document.getElementById("message-input");
  const message = (input.value || "").trim();

  if (!message) {
    showToast("Message cannot be empty.", "error");
    return;
  }

  if (!state.sessionId) {
    showToast("Please join the chat first.", "error");
    return;
  }

  try {
    const result = await fetchJson("/api/send", {
      method: "POST",
      body: JSON.stringify({
        sessionId: state.sessionId,
        username: state.currentUser?.username,
        room: state.currentRoom,
        message,
      }),
    });

    if (!result.success) {
      showToast(result.message || "Unable to send message.", "error");
      return;
    }

    input.value = "";
    showToast("Message sent.", "success");
    await refreshState();
  } catch (error) {
    showToast(error.message || "Unable to send message.", "error");
  }
}

async function refreshState() {
  if (!state.sessionId) {
    return;
  }

  try {
    const result = await fetchJson(`/api/state?session=${encodeURIComponent(state.sessionId)}`);
    if (result.state) {
      applyState(result.state);
    }
  } catch (error) {
    console.error("Refresh failed", error);
  }
}

async function handleSearch() {
  const q = searchInput.value.trim();
  if (!q) {
    searchResults.classList.add("hidden");
    searchResults.innerHTML = "";
    return;
  }

  try {
    const result = await fetchJson(`/api/search?q=${encodeURIComponent(q)}`);
    searchResults.replaceChildren();
    const addSection = (title, entries, getText, type) => {
      if (!entries?.length) return;
      const heading = document.createElement("div");
      const strong = document.createElement("strong");
      strong.textContent = title;
      heading.appendChild(strong);
      searchResults.appendChild(heading);
      entries.forEach((entry) => {
        const item = document.createElement("div");
        item.className = "result-item";
        item.dataset.type = type;
        item.dataset.room = entry.room || entry.name || "";
        item.dataset.user = entry.username || "";
        item.textContent = getText(entry);
        searchResults.appendChild(item);
      });
    };

    addSection("Rooms", result.rooms, (room) => `${room.name} — ${room.description}`, "room");
    addSection("Users", result.users, (user) => user.displayName || user.username, "user");
    addSection("Messages", result.messages, (message) => `${message.room}: ${message.text}`, "message");

    if (!searchResults.childElementCount) {
      const empty = document.createElement("div");
      empty.className = "result-item";
      empty.textContent = "No matching results found.";
      searchResults.appendChild(empty);
    }

    searchResults.classList.remove("hidden");
  } catch (error) {
    console.error(error);
  }
}

async function logoutUser() {
  if (!state.sessionId) {
    showView("landing");
    return;
  }

  try {
    await fetchJson("/api/logout", {
      method: "POST",
      body: JSON.stringify({ sessionId: state.sessionId }),
    });
  } catch (error) {
    console.error(error);
  }

  if (state.eventSource) {
    state.eventSource.close();
  }
  state.sessionId = "";
  state.currentUser = null;
  state.rooms = [];
  state.users = [];
  state.messages = [];
  searchInput.value = "";
  searchResults.classList.add("hidden");
  showView("landing");
  document.getElementById("login-form").reset();
}

function openModal(id) {
  const modal = document.getElementById(id);
  if (modal) {
    modal.classList.remove("hidden");
  }
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) {
    modal.classList.add("hidden");
  }
}

function bindEvents() {
  document.getElementById("start-chat-btn").addEventListener("click", () => showView("auth"));
  document.getElementById("hero-start").addEventListener("click", () => showView("auth"));
  document.getElementById("how-it-works-btn").addEventListener("click", () => {
    document.getElementById("features").scrollIntoView({ behavior: "smooth" });
  });
  document.getElementById("back-to-home").addEventListener("click", () => showView("landing"));
  document.getElementById("login-form").addEventListener("submit", loginUser);

  document.getElementById("send-btn").addEventListener("click", sendMessage);
  document.getElementById("message-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      sendMessage();
    }
  });

  document.getElementById("emoji-btn").addEventListener("click", () => {
    const input = document.getElementById("message-input");
    input.value += "☺";
    input.focus();
  });

  document.getElementById("logout-btn").addEventListener("click", logoutUser);
  document.getElementById("settings-btn").addEventListener("click", () => openModal("settings-modal"));
  document.getElementById("profile-btn").addEventListener("click", () => openModal("profile-modal"));
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const nextTheme = document.body.classList.contains("light-mode") ? "dark" : "light";
    setTheme(nextTheme);
  });

  themeSelect.addEventListener("change", (event) => {
    setTheme(event.target.value);
  });

  document.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", () => closeModal(button.dataset.close));
  });

  roomList.addEventListener("click", (event) => {
    const roomButton = event.target.closest(".room-item");
    if (roomButton) {
      handleRoomSwitch(roomButton.dataset.room);
    }
  });

  searchInput.addEventListener("input", () => {
    clearTimeout(searchInput._searchTimer);
    searchInput._searchTimer = setTimeout(handleSearch, 220);
  });

  searchResults.addEventListener("click", (event) => {
    const item = event.target.closest(".result-item");
    if (!item) return;
    const roomName = item.dataset.room;
    if (item.dataset.type === "room" && roomName) {
      handleRoomSwitch(roomName);
      searchResults.classList.add("hidden");
      searchInput.value = "";
    }
  });
}

applySavedTheme();
showView("landing");
bindEvents();
