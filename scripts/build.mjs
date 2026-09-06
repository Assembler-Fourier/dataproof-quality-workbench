import {cp, mkdir, rm} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("..", import.meta.url));
await mkdir(path.join(root, "public", "contracts"), {recursive: true});
await cp(path.join(root, "contracts", "orders.json"), path.join(root, "public", "contracts", "orders.json"));
await rm(path.join(root, "dist"), {recursive: true, force: true});
await cp(path.join(root, "public"), path.join(root, "dist"), {recursive: true});
console.log("Built static workbench in dist/");
