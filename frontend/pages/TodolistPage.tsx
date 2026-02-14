import { ListTodo } from 'lucide-react';

export function TodolistPage() {
  return (
    <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
      <ListTodo size={48} className="mb-4 text-zinc-600" />
      <p className="text-lg font-medium text-zinc-400">Todolist</p>
      <p className="text-sm mt-1">Coming soon</p>
    </div>
  );
}
