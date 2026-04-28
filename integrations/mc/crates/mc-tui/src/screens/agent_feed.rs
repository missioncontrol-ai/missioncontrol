use ratatui::{
    buffer::Buffer,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, BorderType, Borders, List, ListItem, ListState, Paragraph, Widget},
};
use serde::{Deserialize, Serialize};

use crate::theme;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeedEvent {
    pub ts: String,
    pub agent_id: Option<String>,
    pub mission_id: Option<String>,
    pub event_type: String,
    pub data: String,
}

#[derive(Debug, Default)]
pub struct AgentFeedState {
    pub events: Vec<FeedEvent>,
    pub paused: bool,
    pub selection: usize,
    pub live: bool,
    /// Counts events received while paused (shows user the stream is alive)
    pub buffered_while_paused: usize,
    /// Cap the in-memory ring buffer
    max_events: usize,
}

impl AgentFeedState {
    pub fn new() -> Self {
        Self { max_events: 500, live: false, ..Default::default() }
    }

    pub fn push_event(&mut self, event: FeedEvent) {
        if self.paused {
            self.buffered_while_paused += 1;
            return;
        }
        self.events.push(event);
        if self.events.len() > self.max_events {
            self.events.remove(0);
        }
        // Keep selection at the tail when not scrolled up
        let len = self.events.len();
        if len > 0 && self.selection + 5 >= len.saturating_sub(5) {
            self.selection = len - 1;
        }
    }

    pub fn handle_key(&mut self, key: crossterm::event::KeyCode) -> bool {
        use crossterm::event::KeyCode::*;
        match key {
            Char('p') => {
                self.paused = !self.paused;
                if !self.paused { self.buffered_while_paused = 0; }
                true
            }
            Char('c') => {
                self.events.clear();
                self.selection = 0;
                self.buffered_while_paused = 0;
                true
            }
            Up => {
                if self.selection > 0 { self.selection -= 1; }
                true
            }
            Down => {
                if self.selection + 1 < self.events.len() { self.selection += 1; }
                true
            }
            _ => false,
        }
    }
}

pub struct AgentFeed<'a> {
    pub state: &'a AgentFeedState,
}

impl<'a> Widget for AgentFeed<'a> {
    fn render(self, area: Rect, buf: &mut Buffer) {
        let bg = Block::default().style(theme::normal());
        bg.render(area, buf);

        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Length(3), Constraint::Fill(1), Constraint::Length(1)])
            .split(area);

        render_filter_bar(buf, chunks[0], self.state);
        render_feed(buf, chunks[1], self.state);
        render_hints(buf, chunks[2], self.state);
    }
}

fn render_filter_bar(buf: &mut Buffer, area: Rect, state: &AgentFeedState) {
    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(theme::border_normal())
        .style(theme::normal());
    let inner = block.inner(area);
    block.render(area, buf);

    let live_span = if state.live && !state.paused {
        Span::styled(" ● LIVE ", Style::default().fg(theme::OK).add_modifier(Modifier::BOLD))
    } else if state.paused {
        let buf_count = if state.buffered_while_paused > 0 {
            format!(" PAUSED (+{})", state.buffered_while_paused)
        } else {
            " PAUSED".to_string()
        };
        Span::styled(buf_count, Style::default().fg(theme::WARN).add_modifier(Modifier::BOLD))
    } else {
        Span::styled(" CONNECTING ", Style::default().fg(theme::TEXT_DIM))
    };

    let count = Span::styled(
        format!("  {} events", state.events.len()),
        theme::dim(),
    );

    let line = Line::from(vec![live_span, count]);
    Paragraph::new(line).style(theme::normal()).render(inner, buf);
}

fn render_feed(buf: &mut Buffer, area: Rect, state: &AgentFeedState) {
    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(theme::border_focused())
        .title(Span::styled(" Agent Feed ", theme::panel_title()))
        .style(theme::normal());
    let inner = block.inner(area);
    block.render(area, buf);

    if state.events.is_empty() {
        let msg = if state.live { "waiting for events…" } else { "connecting to backend…" };
        Paragraph::new(Span::styled(msg, theme::dim()))
            .style(theme::normal())
            .render(inner, buf);
        return;
    }

    let items: Vec<ListItem> = state.events.iter().enumerate().map(|(i, ev)| {
        let selected = i == state.selection;
        let style = if selected { theme::selected() } else { theme::normal() };
        let agent = ev.agent_id.as_deref().unwrap_or("?");
        let (type_style, type_str) = event_style(&ev.event_type);

        ListItem::new(Line::from(vec![
            Span::styled(format!("{:<22}", truncate(&ev.ts, 20)), theme::dim()),
            Span::styled(format!("[{:<14}]", truncate(agent, 12)), theme::muted()),
            Span::styled(format!(" {:<20} ", truncate(type_str, 18)), type_style),
            Span::styled(truncate(&ev.data, 40), style),
        ]))
    }).collect();

    let mut ls = ListState::default().with_selected(Some(state.selection));
    ratatui::widgets::StatefulWidget::render(
        List::new(items).style(theme::normal()),
        inner, buf, &mut ls,
    );
}

fn render_hints(buf: &mut Buffer, area: Rect, _state: &AgentFeedState) {
    let hints = Line::from(vec![
        Span::styled("  p ", theme::accent()),
        Span::styled("pause/resume  ", theme::dim()),
        Span::styled("  c ", theme::accent()),
        Span::styled("clear  ", theme::dim()),
        Span::styled("  ↑↓ ", theme::accent()),
        Span::styled("scroll  ", theme::dim()),
        Span::styled("  m/a/q ", theme::muted()),
        Span::styled("navigate", theme::muted()),
    ]);
    Paragraph::new(hints).style(theme::normal()).render(area, buf);
}

fn event_style(event_type: &str) -> (ratatui::style::Style, &str) {
    match event_type {
        "step_started" => (theme::accent(), "step_started"),
        "step_finished" => (theme::ok(), "step_finished"),
        "task_finished" => (Style::default().fg(theme::ACCENT).add_modifier(Modifier::BOLD), "task_finished"),
        "approval_needed" => (theme::warn(), "approval_needed"),
        "artifact_produced" => (theme::purple(), "artifact_produced"),
        "task_claimed" => (theme::ok(), "task_claimed"),
        "heartbeat" => (theme::dim(), "heartbeat"),
        "kluster_started" => (theme::accent(), "kluster_started"),
        "mission_started" => (theme::accent(), "mission_started"),
        _ => (theme::muted(), event_type),
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max { s.to_string() }
    else { format!("{}…", &s[..max.saturating_sub(1)]) }
}
