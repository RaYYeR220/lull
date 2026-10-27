import { cx } from "@/lib/cx";
import { NAV_LINKS } from "@/lib/site";
import { NavLink } from "./NavLink";
import { Wordmark } from "./Wordmark";
import styles from "./Nav.module.css";

/**
 * The site header: wordmark left, primary nav right. It lies over the top
 * 96px of the page (var(--head-h)); pages start their content below it.
 */
export function Nav() {
  return (
    <header className={styles.head}>
      <div className={cx("wrap", styles.bar)}>
        <Wordmark className="reveal" />
        <nav aria-label="Primary" className={cx(styles.nav, "reveal")}>
          <ul>
            {NAV_LINKS.map((link) => (
              <li key={link.href}>
                <NavLink href={link.href} className={styles.link}>
                  {link.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </header>
  );
}
