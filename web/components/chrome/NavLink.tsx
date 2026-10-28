"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { prefetchFor } from "@/lib/site";

/** A nav link that marks itself as the current page (and stays marked on its sub-pages). */
export function NavLink({ href, className, children }: { href: string; className?: string; children: ReactNode }) {
  const pathname = usePathname();
  const current = pathname === href || pathname.startsWith(`${href}/`);
  return (
    <Link href={href} prefetch={prefetchFor(href)} className={className} aria-current={current ? "page" : undefined}>
      {children}
    </Link>
  );
}
