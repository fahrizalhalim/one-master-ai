"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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
  const [loadedForToken, setLoadedForToken] = useState<string | null>(null);

  const tokenRef = useRef(token);
  tokenRef.current = token;

  const headers = useCallback(
    () => ({ Authorization: `Bearer ${token}` }),
    [token]
  );

  // Reset histori setiap kali akun/token berubah.
  useEffect(() => {
    setConversations([]);
    setActiveId(null);
    setLoadedForToken(null);

    if (!token) return;

    let active = true;

    async function load() {
      try {
        const res = await fetch(`${API_URL}/api/v1/conversations`, {
          headers: { Authorization: `Bearer ${token}` },
          cache: "no-store",
        });

        if (!active || tokenRef.current !== token) return;

        if (res.ok) {
          setConversations(await res.json());
          setLoadedForToken(token);
        }
      } catch {
        if (active && tokenRef.current === token) {
          setConversations([]);
        }
      }
    }

    load();

    return () => {
      active = false;
    };
  }, [token]);

  const refresh = useCallback(async () => {
    if (!token) return;

    try {
      const res = await fetch(`${API_URL}/api/v1/conversations`, {
        headers: headers(),
        cache: "no-store",
      });

      if (res.ok && tokenRef.current === token) {
        setConversations(await res.json());
        setLoadedForToken(token);
      }
    } catch {
      // Histori akan dimuat ulang pada kesempatan berikutnya.
    }
  }, [token, headers]);

  const loadConversation = useCallback(
    async (id: string): Promise<ConvMessage[] | null> => {
      if (!token) return null;

      try {
        const res = await fetch(`${API_URL}/api/v1/conversations/${id}`, {
          headers: headers(),
          cache: "no-store",
        });

        if (!res.ok || tokenRef.current !== token) return null;

        const detail = await res.json();

        if (tokenRef.current !== token) return null;

        setActiveId(id);
        return detail.messages;
      } catch {
        return null;
      }
    },
    [token, headers]
  );

  const newConversation = useCallback(() => {
    setActiveId(null);
  }, []);

  const deleteConversation = useCallback(
    async (id: string) => {
      if (!token) return;

      const res = await fetch(`${API_URL}/api/v1/conversations/${id}`, {
        method: "DELETE",
        headers: headers(),
      });

      if (!res.ok || tokenRef.current !== token) return;

      setConversations((c) => c.filter((x) => x.id !== id));

      if (activeId === id) setActiveId(null);
    },
    [token, activeId, headers]
  );

  return {
    conversations: loadedForToken === token ? conversations : [],
    activeId: loadedForToken === token ? activeId : null,
    setActiveId,
    refresh,
    loadConversation,
    newConversation,
    deleteConversation,
  };
}
