use ratatui::{
    buffer::Buffer,
    layout::{Constraint, Direction, Layout, Rect},
    text::{Line, Span},
    widgets::{Block, BorderType, Borders, List, ListItem, ListState, Paragraph, Widget},
};
use serde::{Deserialize, Serialize};

use crate::theme;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReceiptEntry {
    pub id: String,
    pub created_at: String,
    pub mission_name: Option<String>,
    pub task_title: Option<String>,
    pub agent_id: Option<String>,
    pub capability: Option<String>,
    pub outcome: String,
    pub duration_ms: Option<u64>,
    pub artifact_count: u32,
    pub output_summary: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Focus {
    List,
    Detail,
}

#[derive(Debug, Default)]
pub struct ReceiptsState {
    pub focus: Focus,
    pub entries: Vec<ReceiptEntry>,
    pub selection: usize,
    pub loading: bool,
    pub filter_status: Option<String>,
}

impl Default for Focus {
    fn default() -> Self { Focus::List }
}

impl ReceiptsState {
    pub fn handle_key(&mut self, key: crossterm::event::KeyCode) -> bool {
        use crossterm::event::KeyCode::*;
        match key {
            Tab => {
                self.focus = match self.focus {
                    Focus::List => Focus::Detail,
                    Focus::Detail => Focus::List,
                };
                true
            }
            Up if self.focus == Focus::List => {
                if self.selection > 0 { self.selection -= 1; }
                true
            }
            Down if self.focus == Focus::List => {
                if self.selection + 1 < self.entries.len() { self.selection += 1; }
                true
            }
            _ => false,
        }
    }

    pub fn selected(&self) -> Option<&ReceiptEntry> {
        self.entries.get(self.selection)
    }
}

pub struct ReceiptsScreen<'a> {
    pub state: &'a ReceiptsState,
}

impl<'a> Widget for ReceiptsScreen<'a> {
    fn render(self, area: Rect, buf: &mut Buffer) {
        let bg = Block::default().style(theme::normal());
        bg.render(area, buf);

        let chunks = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(55), Constraint::Percentage(45)])
            .split(area);

        render_list(buf, chunks[0], self.state);
        render_detail(buf, chunks[1], self.state);
    }
}

fn render_list(buf: &mut Buffer, area: Rect, state: &ReceiptsState) {
    let focused = state.focus == Focus::List;
    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(theme::border_for(focused))
        .title(Span::styled(" Execution Receipts ", theme::panel_title()))
        .style(theme::normal());
    let inner = block.inner(area);
    block.render(area, buf);

    if state.loading {
        Paragraph::new(Span::styled("loading…", theme::dim()))
            .style(theme::normal())
            .render(inner, buf);
        return;
    }

    if state.entries.is_empty() {
        Paragraph::new(Span::styled("no receipts yet", theme::muted()))
            .style(theme::normal())
            .render(inner, buf);
        return;
    }

    // Header
    let header_area = Rect { height: 1, ..inner };
    let content_area = Rect { y: inner.y + 1, height: inner.height.saturating_sub(1), ..inner };

    let header = Line::from(vec![
        Span::styled(format!("{:<22} ", "Time"), theme::muted()),
        Span::styled(format!("{:<20} ", "Task"), theme::muted()),
        Span::styled(format!("{:<10} ", "Duration"), theme::muted()),
        Span::styled("Status", theme::muted()),
    ]);
    Paragraph::new(header).style(theme::normal()).render(header_area, buf);

    let items: Vec<ListItem> = state.entries.iter().enumerate().map(|(i, e)| {
        let selected = i == state.selection && focused;
        let style = if selected { theme::selected() } else { theme::normal() };
        let dur = e.duration_ms.map(|ms| format!("{ms}ms")).unwrap_or_else(|| "—".into());
        let dot = outcome_dot(&e.outcome);
        let dot_style = outcome_style(&e.outcome);

        ListItem::new(Line::from(vec![
            Span::styled(format!("{:<22} ", truncate(&e.created_at, 20)), style),
            Span::styled(
                format!("{:<20} ", truncate(e.task_title.as_deref().unwrap_or("—"), 18)),
                style,
            ),
            Span::styled(format!("{:<10} ", dur), theme::dim()),
            Span::styled(dot, dot_style),
            Span::styled(format!(" {}", &e.outcome), style),
        ]))
    }).collect();

    let mut ls = ListState::default().with_selected(
        if focused { Some(state.selection) } else { None }
    );
    ratatui::widgets::StatefulWidget::render(
        List::new(items).style(theme::normal()),
        content_area, buf, &mut ls,
    );
}

fn render_detail(buf: &mut Buffer, area: Rect, state: &ReceiptsState) {
    let focused = state.focus == Focus::Detail;
    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(theme::border_for(focused))
        .title(Span::styled(" Receipt Detail ", theme::panel_title()))
        .style(theme::normal());
    let inner = block.inner(area);
    block.render(area, buf);

    let Some(e) = state.selected() else {
        Paragraph::new(Span::styled("select a receipt", theme::muted()))
            .style(theme::normal())
            .render(inner, buf);
        return;
    };

    let dur = e.duration_ms.map(|ms| format!("{ms}ms")).unwrap_or_else(|| "—".into());
    let mut lines: Vec<Line> = vec![
        Line::from(vec![
            Span::styled("ID      ", theme::muted()),
            Span::styled(truncate(&e.id, 20), theme::accent()),
        ]),
        Line::from(vec![
            Span::styled("Time    ", theme::muted()),
            Span::styled(e.created_at.clone(), theme::dim()),
        ]),
        Line::from(vec![
            Span::styled("Duration", theme::muted()),
            Span::styled(dur, theme::dim()),
        ]),
        Line::from(vec![
            Span::styled("Status  ", theme::muted()),
            Span::styled(outcome_dot(&e.outcome), outcome_style(&e.outcome)),
            Span::styled(format!(" {}", e.outcome), outcome_style(&e.outcome)),
        ]),
        Line::from(vec![
            Span::styled("Agent   ", theme::muted()),
            Span::styled(e.agent_id.as_deref().unwrap_or("—"), theme::dim()),
        ]),
        Line::from(vec![
            Span::styled("Mission ", theme::muted()),
            Span::styled(e.mission_name.as_deref().unwrap_or("—"), theme::dim()),
        ]),
        Line::from(vec![
            Span::styled("Artifacts", theme::muted()),
            Span::styled(e.artifact_count.to_string(), theme::purple()),
        ]),
        Line::from(""),
    ];

    if let Some(summary) = &e.output_summary {
        lines.push(Line::from(Span::styled("Output Summary", theme::muted())));
        for part in summary.lines().take(8) {
            lines.push(Line::from(Span::styled(part.to_string(), theme::dim())));
        }
    }

    Paragraph::new(lines)
        .wrap(ratatui::widgets::Wrap { trim: true })
        .style(theme::normal())
        .render(inner, buf);
}

fn outcome_dot(outcome: &str) -> &'static str {
    match outcome.to_lowercase().as_str() {
        "success" | "ok" | "approved" => "●",
        "failed" | "error" | "denied" => "●",
        _ => "○",
    }
}

fn outcome_style(outcome: &str) -> ratatui::style::Style {
    match outcome.to_lowercase().as_str() {
        "success" | "ok" | "approved" => theme::ok(),
        "failed" | "error" | "denied" => theme::err(),
        _ => theme::warn(),
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max { s.to_string() }
    else { format!("{}…", &s[..max.saturating_sub(1)]) }
}
