'use client';

import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { NavigationLink as Link } from './navigation-link';
import { routeHref } from '@/config/routes';
import { adapter } from '@/lib/data';
import { errorMessage } from '@/lib/api/errors';
import type { NotificationCategory, NotificationItem, Role, User } from '@/lib/domain';
import { canOpenRoute, matchRoute } from '@/lib/navigation';
import { emitNotificationStateChanged, onNotificationStateChanged } from '@/lib/notification-events';
import {
  countNotificationsByCategory,
  filterNotifications,
  formatNotificationTimeAgo,
  formatUnreadBadgeCount,
  getNotificationCategoryBadgeClass,
  getNotificationCategoryIconName,
  getNotificationCategoryShortLabel,
  NOTIFICATION_CATEGORIES,
  PRIMARY_NOTIFICATION_CATEGORIES,
} from '@/lib/notifications';
import { Icon } from './ui';
import './notification-bell.css';

const POLL_INTERVAL_MS = 30_000;
const MIN_REFRESH_GAP_MS = 2_000;

export function NotificationBell({
  user,
  role,
  currentRouteId,
}: {
  user: User;
  role: Role;
  currentRouteId: string;
}) {
  const router = useRouter();
  const popoverId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const lastFetchedAtRef = useRef(0);
  const hasItemsRef = useRef(false);

  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [busyAll, setBusyAll] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState<'ALL' | NotificationCategory>('ALL');

  const notificationsHref = routeHref('account.notifications') ?? '/notifications';

  const loadNotifications = useCallback(async (force = false) => {
    if (user.status !== 'ACTIVE') return;
    const now = Date.now();
    if (!force && now - lastFetchedAtRef.current < MIN_REFRESH_GAP_MS) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const expectedGen = ++generationRef.current;
    setLoading(prev => prev || !hasItemsRef.current);
    setError(null);

    try {
      const page = await adapter.notifications.list({ limit: 40, signal: controller.signal });
      if (generationRef.current !== expectedGen || controller.signal.aborted) return;
      lastFetchedAtRef.current = Date.now();
      hasItemsRef.current = page.items.length > 0;
      setItems(page.items);
      setUnreadCount(page.unreadCount);
      setError(null);
    } catch (err) {
      if (controller.signal.aborted || generationRef.current !== expectedGen) return;
      setError(errorMessage(err));
    } finally {
      if (generationRef.current === expectedGen && !controller.signal.aborted) {
        setLoading(false);
      }
    }
  }, [user.id, user.status]);

  useEffect(() => {
    hasItemsRef.current = false;
    setItems([]);
    setUnreadCount(0);
    setOpen(false);
    setCategoryFilter('ALL');
    lastFetchedAtRef.current = 0;
    void loadNotifications(true);
    return () => {
      abortRef.current?.abort();
      generationRef.current += 1;
    };
  }, [user.id, user.status, role, loadNotifications]);

  useEffect(() => {
    setOpen(false);
    void loadNotifications(false);
  }, [currentRouteId, loadNotifications]);

  useEffect(() => {
    const unsubscribe = onNotificationStateChanged(event => {
      if (event.source === 'bell') return;
      if (event.unreadCount !== undefined) {
        setUnreadCount(event.unreadCount);
      }
      if (event.markAll) {
        setItems(prev => prev.map(item => (item.isRead ? item : { ...item, isRead: true })));
      } else if (event.readId) {
        setItems(prev => prev.map(item => (item.id === event.readId ? { ...item, isRead: true } : item)));
      }
      void loadNotifications(true);
    });
    return unsubscribe;
  }, [loadNotifications]);

  useEffect(() => {
    const onFocusOrVisible = () => {
      if (document.visibilityState === 'visible') {
        void loadNotifications(false);
      }
    };
    window.addEventListener('focus', onFocusOrVisible);
    document.addEventListener('visibilitychange', onFocusOrVisible);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        void loadNotifications(true);
      }
    }, POLL_INTERVAL_MS);
    return () => {
      window.removeEventListener('focus', onFocusOrVisible);
      document.removeEventListener('visibilitychange', onFocusOrVisible);
      window.clearInterval(timer);
    };
  }, [loadNotifications]);

  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  if (user.status !== 'ACTIVE') return null;

  const badgeText = formatUnreadBadgeCount(unreadCount);
  const unreadItems = items.filter(item => !item.isRead);
  const categoryCounts = countNotificationsByCategory(unreadItems);
  const visibleCategories = NOTIFICATION_CATEGORIES.filter(
    cat => PRIMARY_NOTIFICATION_CATEGORIES.includes(cat) || categoryCounts[cat] > 0,
  );
  const filteredUnread = filterNotifications(unreadItems, {
    readFilter: 'unread',
    category: categoryFilter,
  });

  function handleToggle() {
    const nextOpen = !open;
    setOpen(nextOpen);
    if (nextOpen) {
      void loadNotifications(true);
    }
  }

  async function markOne(id: string): Promise<boolean> {
    if (busyId || busyAll) return false;
    setBusyId(id);
    setError(null);
    try {
      await adapter.notifications.markAsRead(id);
      const nextUnread = Math.max(0, unreadCount - 1);
      setItems(prev => prev.map(item => (item.id === id ? { ...item, isRead: true } : item)));
      setUnreadCount(nextUnread);
      emitNotificationStateChanged({ source: 'bell', readId: id, unreadCount: nextUnread });
      return true;
    } catch (err) {
      setError(errorMessage(err));
      return false;
    } finally {
      setBusyId(null);
    }
  }

  async function handleOpenItem(item: NotificationItem) {
    if (!item.isRead) {
      await markOne(item.id);
    }
    setOpen(false);
    if (item.targetHref) {
      const targetRoute = matchRoute(item.targetHref);
      if (targetRoute && canOpenRoute(targetRoute, user.roles)) {
        router.push(item.targetHref);
        return;
      }
    }
    router.push(notificationsHref);
  }

  async function handleMarkAll() {
    if (busyAll || unreadCount === 0) return;
    setBusyAll(true);
    setError(null);
    try {
      await adapter.notifications.markAllAsRead();
      setItems(prev => prev.map(item => (item.isRead ? item : { ...item, isRead: true })));
      setUnreadCount(0);
      emitNotificationStateChanged({ source: 'bell', markAll: true, unreadCount: 0 });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyAll(false);
    }
  }

  return (
    <div ref={rootRef} className="lumi-bell-root">
      <button
        ref={triggerRef}
        type="button"
        className={`icon-button lumi-bell-trigger${open ? ' is-open' : ''}`}
        aria-label={unreadCount > 0 ? `Thông báo (${unreadCount} chưa đọc)` : 'Thông báo'}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={popoverId}
        onClick={handleToggle}
      >
        <Icon name="bell" />
        {badgeText && (
          <span className="lumi-bell-badge" aria-hidden="true">
            {badgeText}
          </span>
        )}
      </button>

      {open && (
        <div
          id={popoverId}
          className="lumi-bell-popover card"
          role="dialog"
          aria-label="Thông báo chưa đọc"
        >
          <header className="lumi-bell-popover-header">
            <div className="lumi-bell-popover-title-wrap">
              <strong>Thông báo chưa đọc</strong>
              <span className="lumi-bell-popover-count">{unreadCount}</span>
            </div>
            <button
              type="button"
              className="lumi-bell-markall-btn"
              disabled={unreadCount === 0 || busyAll}
              onClick={() => void handleMarkAll()}
            >
              <Icon name="check" />
              <span>{busyAll ? 'Đang xử lý…' : 'Đọc tất cả'}</span>
            </button>
          </header>

          <div className="lumi-bell-categories" role="tablist" aria-label="Phân loại thông báo chưa đọc">
            <button
              type="button"
              role="tab"
              aria-selected={categoryFilter === 'ALL'}
              className={`lumi-bell-cat-chip${categoryFilter === 'ALL' ? ' is-active' : ''}`}
              onClick={() => setCategoryFilter('ALL')}
            >
              <span>Tất cả</span>
              <span className="lumi-bell-cat-count">{unreadItems.length}</span>
            </button>
            {visibleCategories.map(category => (
              <button
                key={category}
                type="button"
                role="tab"
                aria-selected={categoryFilter === category}
                className={`lumi-bell-cat-chip${categoryFilter === category ? ' is-active' : ''}`}
                onClick={() => setCategoryFilter(category)}
              >
                <span>{getNotificationCategoryShortLabel(category)}</span>
                {categoryCounts[category] > 0 && (
                  <span className="lumi-bell-cat-count">{categoryCounts[category]}</span>
                )}
              </button>
            ))}
          </div>

          <div className="lumi-bell-list" role="list" aria-label="Danh sách thông báo chưa đọc">
            {loading && unreadItems.length === 0 ? (
              <div className="lumi-bell-state" role="status">
                <span className="loader" aria-hidden="true" />
                <span>Đang tải thông báo…</span>
              </div>
            ) : error ? (
              <div className="lumi-bell-state" role="alert">
                <strong>Không thể tải thông báo</strong>
                <p>{error}</p>
                <button
                  type="button"
                  className="button secondary small"
                  onClick={() => void loadNotifications(true)}
                >
                  Thử lại
                </button>
              </div>
            ) : filteredUnread.length === 0 ? (
              <div className="lumi-bell-state" role="status">
                <strong>
                  {categoryFilter === 'ALL'
                    ? unreadCount > 0
                      ? 'Các thông báo gần đây đều đã đọc'
                      : 'Không có thông báo chưa đọc'
                    : `Không có thông báo "${getNotificationCategoryShortLabel(categoryFilter)}" chưa đọc`}
                </strong>
                <p>
                  {categoryFilter === 'ALL'
                    ? unreadCount > 0
                      ? `Bạn còn ${unreadCount} thông báo chưa đọc cũ hơn trong hộp thư.`
                      : 'Khi giáo viên cập nhật lịch, bài tập, luyện tập hoặc kiểm tra sẽ hiển thị tại đây.'
                    : 'Thử chọn danh mục khác hoặc mở trang tất cả thông báo.'}
                </p>
              </div>
            ) : (
              filteredUnread.map(item => (
                <div key={item.id} role="listitem" className="lumi-bell-item">
                  <button
                    type="button"
                    className="lumi-bell-item-main"
                    disabled={busyId === item.id || busyAll}
                    onClick={() => void handleOpenItem(item)}
                  >
                    <div className="lumi-bell-item-meta">
                      <div className="lumi-bell-item-badges">
                        <span className="lumi-bell-unread-dot" aria-hidden="true" />
                        <span className={`lumi-notification-badge ${getNotificationCategoryBadgeClass(item.category)}`}>
                          <Icon name={getNotificationCategoryIconName(item.category)} />
                          <span>{getNotificationCategoryShortLabel(item.category)}</span>
                        </span>
                        {item.className && <span className="lumi-notification-class-tag">{item.className}</span>}
                      </div>
                      <time className="lumi-bell-item-time">
                        {formatNotificationTimeAgo(item.createdAt)}
                      </time>
                    </div>
                    <span className="lumi-bell-item-title">{item.title}</span>
                    <span className="lumi-bell-item-snippet">{item.content}</span>
                  </button>

                  <button
                    type="button"
                    className="lumi-bell-item-read-btn"
                    title="Đánh dấu đã đọc"
                    aria-label={`Đánh dấu đã đọc: ${item.title}`}
                    disabled={busyId === item.id || busyAll}
                    onClick={e => {
                      e.stopPropagation();
                      void markOne(item.id);
                    }}
                  >
                    <Icon name="check" />
                  </button>
                </div>
              ))
            )}
          </div>

          <footer className="lumi-bell-popover-footer">
            <Link
              href={notificationsHref}
              className="lumi-bell-footer-link"
              onClick={() => setOpen(false)}
            >
              <span>Xem tất cả thông báo</span>
              <Icon name="chevronRight" />
            </Link>
          </footer>
        </div>
      )}
    </div>
  );
}
