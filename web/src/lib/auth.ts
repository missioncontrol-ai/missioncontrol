import { browser } from '$app/environment';
import { derived, writable } from 'svelte/store';

const STORAGE_KEY = 'missioncontrol:token';

function loadToken(): string | null {
  if (!browser) return null;
  return localStorage.getItem(STORAGE_KEY);
}

const tokenStore = writable<string | null>(loadToken());

const authStore = derived(tokenStore, ($token) => ({
  token: $token,
  loggedIn: Boolean($token)
}));

function persistToken(value: string | null) {
  if (!browser) return;
  if (value) {
    localStorage.setItem(STORAGE_KEY, value);
  } else {
    localStorage.removeItem(STORAGE_KEY);
  }
}

export function loginWithToken(value: string) {
  tokenStore.set(value);
  persistToken(value);
}

export function logout() {
  tokenStore.set(null);
  persistToken(null);
}

export function startOidcLogin(redirect = window?.location?.href) {
  const url = `/oidc/authorize?redirect=${encodeURIComponent(redirect)}`;
  window.location.assign(url);
}

export const token = derived(tokenStore, ($token) => $token);
export { authStore };
