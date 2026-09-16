/**
 * 「⌘K 面开着没有」这一个布尔（harness 三期 3c §2.5）。
 *
 * 之所以是 store 而不是一个 prop：打开它的入口有两个——顶栏放大镜和全局
 * ⌘K——而它们在组件树上离得很远（`TopBar` 在布局里，快捷键的监听器在面板自己
 * 身上）。把这个布尔提到公共祖先要穿过整条布局链，而那条链上的每一层都不关心
 * 它。
 *
 * **刻意不加 `persist`**：`settingsStore` 存的是用户挑过的偏好，一个搜索面板的
 * 开合不是偏好。持久化它的后果是刷新后页面上盖着一个你上次忘了关的面板。
 */
import { create } from 'zustand';

interface CommandPaletteState {
  open: boolean;
  setOpen: (open: boolean) => void;
}

export const useCommandPalette = create<CommandPaletteState>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}));
