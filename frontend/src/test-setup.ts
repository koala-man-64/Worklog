import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach } from "vitest";
import { setDataContext } from "./api";
beforeEach(() => setDataContext("test"));
import { cleanup } from "@testing-library/react";
afterEach(() => {
  cleanup();
  localStorage.clear();
});
