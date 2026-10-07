import { html, render } from "../vendor/preact.js";
import { connectEvents } from "./api.js";
import { handleEvent, online } from "./store.js";
import { applyTheme } from "./theme.js";
import { App } from "./components/shell.js";

applyTheme({});
connectEvents(handleEvent, (up) => (online.value = up));
render(html`<${App} />`, document.getElementById("app"));
