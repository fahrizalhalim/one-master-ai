"use client";

import { useCallback, useEffect, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

export type ConvItem = {
  id: string;
  title: string;
  updated_at: string;
};

export type ConvMessage = {
  role: string;
  skill: string;
  content: string;
  created_at: string;
};

export function useConversations(token: string | null) {
  const [conversations, setConversations] = useState<ConvItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  const headers = useCallback(
    () => ({ Authorization: `Bearer ${token}` }),
    [token]
  );

  const refresh = useCallback(async () => {
    if (!token) return;
    const res = await fetch(`${API_URL}/api/v1/conversations`, {
      headers: headers(),
    });
    if (res.ok) setConversations(await res.json());
  }, [token, headers]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const loadConversation = useCallback(
    async (id: string): Promise<ConvMessage[] | null> => {
      const res = await fetch(`${API_URL}/api/v1/conversations/${id}`, {
        headers: headers(),
      });
      if (!res.ok) return null;
      const detail = await res.json();
      setActiveId(id);
      return detail.messages;
    },
    [headers]
  );

  const newConversation = useCallback(() => {
    setActiveId(null);
  }, []);

  const deleteConversation = useCallback(
    async (id: string) => {
      await fetch(`${API_URL}/api/v1/conversations/${id}`, {
        method: "DELETE",
        headers: headers(),
      });
      setConversations((c) => c.filter((x) => x.id !== id));
      if (activeId === id) setActiveId(null);
    },
    [activeId, headers]
  );

  return {
    conversations,
    activeId,
    setActiveId,
    refresh,
    loadConversation,
    newConversation,
    deleteConversation,
  };
}
