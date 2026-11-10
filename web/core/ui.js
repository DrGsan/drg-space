import {formatNumber} from './i18n.js';
import {tText,locale} from "./i18n.js";
import { marked } from "marked";
import DOMPurify from "dompurify";
export const $ = (s, p = document) => p.querySelector(s);
export const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const md = (s) => DOMPurify.sanitize(marked.parse(s || ""));
export const date = (d) =>
  new Date(d * 1000).toLocaleString(locale(), {
    dateStyle: "medium",
    timeStyle: "short",
  });
export const dayKey = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export const today = () => dayKey(new Date());
export const bytes = (n) =>
  n >= 1024 ** 4 ? tText("common:tb", {value0:(formatNumber((n / 1024 ** 4),{minimumFractionDigits:2,maximumFractionDigits:2}))}) :
  n >= 1024 ** 3 ? tText("common:gb", {value0:(formatNumber((n / 1024 ** 3),{minimumFractionDigits:2,maximumFractionDigits:2}))}) :
  n >= 1024 ** 2 ? tText("common:mb", {value0:(formatNumber((n / 1024 ** 2),{minimumFractionDigits:1,maximumFractionDigits:1}))}) :
  n >= 1024 ? tText("common:kb", {value0:(formatNumber((n / 1024),{minimumFractionDigits:1,maximumFractionDigits:1}))}) : tText("common:b", {value0:(n)});
export function toast(text) {
  $("#toast").textContent = text;
  $("#toast").classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $("#toast").classList.remove("show"), 4500);
}
export function formBind(selector, fn) {
  const form = typeof selector === "string" ? $(selector) : selector;
  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const button = $("button[type=submit]", form);
    if (button) button.disabled = true;
    try {
      await fn(new FormData(form), form);
    } catch (error) {
      toast(error.message);
    } finally {
      if (button) button.disabled = false;
    }
  });
}
