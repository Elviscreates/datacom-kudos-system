const state = {
  profiles: [],
  user: null,
  recipient: null,
  cursor: null,
  searchSequence: 0,
  searchTimer: null,
  moderation: null,
  filter: "community",
};

const elements = {
  profileSelect: document.querySelector("#profile-select"),
  profileModal: document.querySelector("#profile-modal"),
  profileOptions: document.querySelector("#profile-options"),
  sidebarName: document.querySelector("#sidebar-name"),
  sidebarRole: document.querySelector("#sidebar-role"),
  sidebarAvatar: document.querySelector("#sidebar-avatar"),
  moderationNav: document.querySelector("#moderation-nav"),
  communityCount: document.querySelector("#community-count"),
  activeFilter: document.querySelector("#active-filter"),
  sidebar: document.querySelector("#sidebar"),
  sidebarBackdrop: document.querySelector("#sidebar-backdrop"),
  mobileMenu: document.querySelector("#mobile-menu-button"),
  form: document.querySelector("#kudos-form"),
  recipientSearch: document.querySelector("#recipient-search"),
  recipientResults: document.querySelector("#recipient-results"),
  selectedRecipient: document.querySelector("#selected-recipient"),
  recipientError: document.querySelector("#recipient-error"),
  message: document.querySelector("#kudos-message"),
  messageError: document.querySelector("#message-error"),
  characterCount: document.querySelector("#character-count"),
  formStatus: document.querySelector("#form-status"),
  submitButton: document.querySelector("#submit-button"),
  feedList: document.querySelector("#feed-list"),
  feedStatus: document.querySelector("#feed-status"),
  loadMore: document.querySelector("#load-more"),
  refresh: document.querySelector("#refresh-feed"),
  moderationDialog: document.querySelector("#moderation-dialog"),
  moderationForm: document.querySelector("#moderation-form"),
  moderationTitle: document.querySelector("#moderation-title"),
  moderationDescription: document.querySelector("#moderation-description"),
  moderationReason: document.querySelector("#moderation-reason"),
  moderationError: document.querySelector("#moderation-error"),
  moderationConfirm: document.querySelector("#confirm-moderation"),
  closeModeration: document.querySelector("#close-moderation"),
  cancelModeration: document.querySelector("#cancel-moderation"),
};

function node(tag, className = "", text = "") {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text) element.textContent = text;
  return element;
}

function initials(name = "") {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join("");
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    credentials: "same-origin",
    cache: "no-store",
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  const data = response.status === 204 ? {} : await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = data.error || {};
    const exception = new Error(error.message || "The request could not be completed.");
    exception.status = response.status;
    exception.code = error.code;
    exception.fieldErrors = error.fieldErrors || {};
    exception.retryAfter = response.headers.get("Retry-After");
    throw exception;
  }
  return data;
}

function setUser(user) {
  state.user = user;
  const name = user?.displayName || "Choose a profile";
  elements.sidebarName.textContent = name;
  elements.sidebarRole.textContent = user?.role || "Local demo";
  elements.sidebarAvatar.textContent = initials(name) || "?";
  elements.profileSelect.value = user?.id || "";
  elements.moderationNav.hidden = user?.role !== "admin";
  if (user?.role !== "admin" && state.filter === "moderation") setFeedFilter("community");
}

function profileOption(profile, onClick) {
  const button = node("button", "profile-option");
  button.type = "button";
  const avatar = node("span", "recipient-mini-avatar", initials(profile.displayName));
  const info = node("span", "option-info");
  info.append(node("strong", "", profile.displayName));
  info.append(node("small", "", profile.team));
  const role = node("span", "role-pill", profile.role);
  button.append(avatar, info, role);
  button.addEventListener("click", onClick);
  return button;
}

async function switchProfile(userId) {
  try {
    const data = await api("/api/auth/switch", {
      method: "POST",
      body: JSON.stringify({ userId }),
    });
    setUser(data.user);
    elements.profileModal.hidden = true;
    elements.formStatus.textContent = "";
    elements.formStatus.style.color = "";
    clearComposer();
    await loadStats();
    await loadFeed(true);
  } catch (error) {
    elements.feedStatus.textContent = error.message;
  }
}

function renderProfiles() {
  elements.profileSelect.replaceChildren();
  for (const profile of state.profiles) {
    const option = node("option", "", `${profile.displayName} · ${profile.role}`);
    option.value = profile.id;
    elements.profileSelect.append(option);
  }
  elements.profileSelect.disabled = false;
  elements.profileOptions.replaceChildren(
    ...state.profiles.map((profile) => profileOption(profile, () => switchProfile(profile.id))),
  );
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
}

function createFeedItem(item) {
  const card = node("article", "kudos-card");
  card.dataset.kudosId = String(item.id);
  const avatar = node("div", "kudos-avatar", initials(item.giver.displayName));
  avatar.setAttribute("aria-hidden", "true");
  const content = node("div", "kudos-content");
  const meta = node("div", "kudos-meta");
  meta.append(node("strong", "", item.giver.displayName));
  meta.append(node("span", "to-text", "celebrated"));
  meta.append(node("span", "recipient-name", item.recipient.displayName));
  meta.append(node("time", "kudos-time", formatTime(item.createdAt)));
  meta.lastChild.dateTime = item.createdAt;
  const message = node("p", "kudos-message", item.message);
  content.append(meta, message);
  card.append(avatar, content);

  if (state.user?.role === "admin") {
    const actions = node("div", "moderation-actions");
    const hide = node("button", "moderation-action", "Hide");
    hide.type = "button";
    hide.setAttribute("aria-label", `Hide kudos from ${item.giver.displayName}`);
    hide.addEventListener("click", () => openModeration(item, "hidden"));
    const remove = node("button", "moderation-action", "Delete");
    remove.type = "button";
    remove.setAttribute("aria-label", `Delete kudos from ${item.giver.displayName}`);
    remove.addEventListener("click", () => openModeration(item, "deleted"));
    actions.append(hide, remove);
    card.append(actions);
  }
  return card;
}

async function loadFeed(reset = false) {
  if (!state.user) return;
  if (state.filter === "moderation") {
    await loadModerationLog();
    return;
  }
  if (reset) {
    state.cursor = null;
    elements.feedList.replaceChildren();
    elements.feedStatus.textContent = "Loading kudos…";
  }
  elements.refresh.disabled = true;
  const params = new URLSearchParams({ limit: "20" });
  params.set("scope", state.filter);
  if (!reset && state.cursor) params.set("cursor", state.cursor);
  try {
    const data = await api(`/api/kudos?${params.toString()}`);
    const items = data.items || [];
    for (const item of items) elements.feedList.append(createFeedItem(item));
    state.cursor = data.nextCursor;
    elements.feedStatus.textContent = elements.feedList.childElementCount ? "" : "No kudos yet. Be the first to celebrate someone.";
    elements.loadMore.hidden = !state.cursor;
  } catch (error) {
    elements.feedStatus.textContent = error.message;
  } finally {
    elements.refresh.disabled = false;
  }
}

async function loadStats() {
  try {
    const data = await api("/api/kudos/stats");
    elements.communityCount.textContent = String(data.visible || 0);
  } catch (error) {
    elements.communityCount.textContent = "0";
  }
}

async function loadModerationLog() {
  elements.feedList.replaceChildren();
  elements.feedStatus.textContent = "Loading moderation log…";
  elements.loadMore.hidden = true;
  try {
    const data = await api("/api/kudos/moderation");
    const items = data.items || [];
    for (const item of items) {
      const card = node("article", "kudos-card moderation-log-card");
      const avatar = node("div", "kudos-avatar", initials(item.giver.displayName));
      const content = node("div", "kudos-content");
      const meta = node("div", "kudos-meta");
      meta.append(node("strong", "", item.giver.displayName));
      meta.append(node("span", "to-text", "celebrated"));
      meta.append(node("span", "recipient-name", item.recipient.displayName));
      content.append(meta);
      content.append(node("p", "kudos-message", item.message));
      content.append(node("p", "moderation-log-reason", `${item.action === "deleted" ? "Deleted" : "Hidden"} · ${item.reason}`));
      card.append(avatar, content);
      elements.feedList.append(card);
    }
    elements.feedStatus.textContent = items.length ? "" : "No moderated kudos.";
  } catch (error) {
    elements.feedStatus.textContent = error.message;
  }
}

function setFeedFilter(filter) {
  state.filter = filter;
  document.querySelectorAll("[data-filter]").forEach((link) => {
    link.classList.toggle("active", link.dataset.filter === filter);
  });
  const labels = {
    community: "Community feed",
    given: "Given by me",
    received: "Received by me",
    moderation: "Moderation log",
  };
  elements.activeFilter.textContent = labels[filter];
  closeMobileSidebar();
  loadFeed(true);
}

function clearComposer() {
  state.recipient = null;
  elements.recipientSearch.value = "";
  elements.recipientResults.hidden = true;
  elements.recipientResults.replaceChildren();
  elements.selectedRecipient.hidden = true;
  elements.selectedRecipient.replaceChildren();
  elements.message.value = "";
  elements.characterCount.textContent = "0 / 500";
  elements.recipientError.textContent = "";
  elements.messageError.textContent = "";
}

function selectRecipient(profile) {
  state.recipient = profile;
  elements.recipientSearch.value = "";
  elements.recipientResults.hidden = true;
  elements.recipientResults.replaceChildren();
  elements.selectedRecipient.replaceChildren(
    node("span", "recipient-mini-avatar", initials(profile.displayName)),
    node("span", "", profile.displayName),
  );
  const remove = node("button", "remove-recipient", "×");
  remove.type = "button";
  remove.setAttribute("aria-label", "Remove selected colleague");
  remove.addEventListener("click", () => {
    state.recipient = null;
    elements.selectedRecipient.hidden = true;
    elements.recipientSearch.focus();
  });
  elements.selectedRecipient.append(remove);
  elements.selectedRecipient.hidden = false;
  elements.recipientError.textContent = "";
}

function renderRecipientResults(users, sequence) {
  if (sequence !== state.searchSequence) return;
  elements.recipientResults.replaceChildren();
  if (!users.length) {
    elements.recipientResults.append(node("div", "feed-status", "No colleagues found."));
  } else {
    for (const profile of users) {
      const option = node("button", "recipient-option");
      option.type = "button";
      option.setAttribute("role", "option");
      option.append(node("span", "recipient-mini-avatar", initials(profile.displayName)));
      const details = node("span");
      details.append(node("strong", "", profile.displayName));
      details.append(node("small", "", profile.team));
      option.append(details);
      option.addEventListener("click", () => selectRecipient(profile));
      elements.recipientResults.append(option);
    }
  }
  elements.recipientResults.hidden = false;
}

elements.recipientSearch.addEventListener("input", () => {
  const query = elements.recipientSearch.value.trim();
  state.recipient = null;
  elements.selectedRecipient.hidden = true;
  elements.recipientError.textContent = "";
  elements.recipientResults.replaceChildren();
  clearTimeout(state.searchTimer);
  const sequence = ++state.searchSequence;
  if (query.length < 2) {
    elements.recipientResults.hidden = true;
    return;
  }
  state.searchTimer = setTimeout(async () => {
    try {
      const data = await api(`/api/users?query=${encodeURIComponent(query)}&limit=10`);
      renderRecipientResults(data.users || [], sequence);
    } catch (error) {
      if (sequence === state.searchSequence) {
        elements.recipientResults.replaceChildren(node("div", "feed-status", error.message));
        elements.recipientResults.hidden = false;
      }
    }
  }, 250);
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".field-group")) elements.recipientResults.hidden = true;
});

elements.message.addEventListener("input", () => {
  elements.characterCount.textContent = `${elements.message.value.length} / 500`;
  elements.messageError.textContent = "";
});

elements.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  elements.formStatus.textContent = "";
  elements.formStatus.style.color = "";
  elements.recipientError.textContent = "";
  elements.messageError.textContent = "";
  if (!state.recipient) {
    elements.recipientError.textContent = "Choose a colleague from the search results.";
    return;
  }
  const message = elements.message.value.trim();
  if (!message) {
    elements.messageError.textContent = "Write a short message before sending.";
    return;
  }
  if (message.length > 500) {
    elements.messageError.textContent = "Messages can be up to 500 characters.";
    return;
  }
  elements.submitButton.disabled = true;
  elements.submitButton.textContent = "Sending…";
  try {
    await api("/api/kudos", {
      method: "POST",
      body: JSON.stringify({ recipientId: state.recipient.id, message }),
    });
    clearComposer();
    elements.formStatus.textContent = "Kudos sent — thanks for spreading the good.";
    await loadStats();
    await loadFeed(true);
  } catch (error) {
    if (error.fieldErrors.recipientId) elements.recipientError.textContent = error.fieldErrors.recipientId;
    if (error.fieldErrors.message) elements.messageError.textContent = error.fieldErrors.message;
    if (error.code === "KUDOS_COOLDOWN" && error.retryAfter) {
      elements.formStatus.textContent = `Please wait ${error.retryAfter} seconds before sending another kudos.`;
    } else {
      elements.formStatus.textContent = error.message;
      elements.formStatus.style.color = "#bf354b";
    }
  } finally {
    elements.submitButton.disabled = false;
    elements.submitButton.textContent = "Send kudos";
  }
});

elements.profileSelect.addEventListener("change", () => {
  if (elements.profileSelect.value !== state.user?.id) switchProfile(elements.profileSelect.value);
});
elements.refresh.addEventListener("click", () => loadFeed(true));
elements.loadMore.addEventListener("click", () => loadFeed(false));
setInterval(() => {
  if (state.user && document.visibilityState === "visible") loadFeed(true);
}, 30_000);
document.addEventListener("visibilitychange", () => {
  if (state.user && document.visibilityState === "visible") loadFeed(true);
});

function openModeration(item, action) {
  state.moderation = { id: item.id, action };
  const isDelete = action === "deleted";
  elements.moderationTitle.textContent = isDelete ? "Delete this kudos?" : "Hide this kudos";
  elements.moderationDescription.textContent = isDelete
    ? `This removes the message from the public feed and keeps an audit record. Message from ${item.giver.displayName}: “${item.message}”`
    : `This removes the message from the public feed and keeps an audit record. Message from ${item.giver.displayName}: “${item.message}”`;
  elements.moderationConfirm.textContent = isDelete ? "Delete kudos" : "Hide kudos";
  elements.moderationReason.value = "";
  elements.moderationError.textContent = "";
  elements.moderationDialog.showModal();
  elements.moderationReason.focus();
}

function closeModeration() {
  elements.moderationDialog.close();
  state.moderation = null;
}

elements.closeModeration.addEventListener("click", closeModeration);
elements.cancelModeration.addEventListener("click", closeModeration);
elements.moderationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.moderation) return;
  const reason = elements.moderationReason.value.trim();
  if (!reason) {
    elements.moderationError.textContent = "A reason is required.";
    return;
  }
  const action = state.moderation.action;
  const id = state.moderation.id;
  elements.moderationConfirm.disabled = true;
  elements.moderationConfirm.textContent = "Saving…";
  try {
    const options = { method: action === "hidden" ? "PATCH" : "DELETE", body: JSON.stringify({ reason }) };
    const path = action === "hidden" ? `/api/kudos/${id}/hide` : `/api/kudos/${id}`;
    await api(path, options);
    closeModeration();
    await loadStats();
    await loadFeed(true);
  } catch (error) {
    elements.moderationError.textContent = error.message;
  } finally {
    elements.moderationConfirm.disabled = false;
    elements.moderationConfirm.textContent = action === "deleted" ? "Delete kudos" : "Hide kudos";
  }
});

function closeMobileSidebar() {
  elements.sidebar.classList.remove("open");
  elements.sidebarBackdrop.hidden = true;
  elements.mobileMenu.setAttribute("aria-expanded", "false");
  elements.mobileMenu.setAttribute("aria-label", "Open navigation");
}

elements.mobileMenu.addEventListener("click", () => {
  const open = !elements.sidebar.classList.contains("open");
  elements.sidebar.classList.toggle("open", open);
  elements.sidebarBackdrop.hidden = !open;
  elements.mobileMenu.setAttribute("aria-expanded", String(open));
  elements.mobileMenu.setAttribute("aria-label", open ? "Close navigation" : "Open navigation");
});
elements.sidebarBackdrop.addEventListener("click", closeMobileSidebar);
document.querySelectorAll("[data-filter]").forEach((link) => {
  link.addEventListener("click", () => setFeedFilter(link.dataset.filter));
});

async function initialize() {
  try {
    const profileData = await api("/api/auth/profiles");
    state.profiles = profileData.profiles || [];
    renderProfiles();
    try {
      const session = await api("/api/auth/me");
      setUser(session.user);
      await loadStats();
      await loadFeed(true);
    } catch (error) {
      if (error.status === 401) {
        elements.profileModal.hidden = false;
      } else {
        elements.feedStatus.textContent = error.message;
      }
    }
  } catch (error) {
    elements.feedStatus.textContent = error.message;
  }
}

initialize();
