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
pub struct ApprovalRequest {
    pub id: String,
    pub task_id: Option<String>,
    pub mission_name: Option<String>,
    pub agent_id: Option<String>,
    pub tool: String,
    pub risk_level: String,
    pub wait_secs: Option<u64>,
    pub reasoning: Option<String>,
    pub input_json: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Focus {
    Queue,
    Detail,
}

#[derive(Debug, Default)]
pub struct ApprovalQueueState {
    pub focus: Focus,
    pub pending: Vec<ApprovalRequest>,
    pub history: Vec<(ApprovalRequest, String)>, // (request, decision)
    pub selection: usize,
    pub loading: bool,
    pub last_error: Option<String>,
    /// Set when a key action was taken; cleared after dispatch.
    pub pending_action: Option<ApprovalAction>,
}

#[derive(Debug, Clone)]
pub struct ApprovalAction {
    pub approval_id: String,
    pub decision: String, // "approve" or "reject"
}

impl Default for Focus {
    fn default() -> Self { Focus::Queue }
}

impl ApprovalQueueState {
    pub fn handle_key(&mut self, key: crossterm::event::KeyCode) -> bool {
        use crossterm::event::KeyCode::*;
        match key {
            Tab => {
                self.focus = match self.focus {
                    Focus::Queue => Focus::Detail,
                    Focus::Detail => Focus::Queue,
                };
                true
            }
            Up if self.focus == Focus::Queue => {
                if self.selection > 0 { self.selection -= 1; }
                true
            }
            Down if self.focus == Focus::Queue => {
                if self.selection + 1 < self.pending.len() { self.selection += 1; }
                true
            }
            Char('y') | Char('Y') => {
                if let Some(req) = self.pending.get(self.selection) {
                    self.pending_action = Some(ApprovalAction {
                        approval_id: req.id.clone(),
                        decision: "approve".to_string(),
                    });
                }
                true
            }
            Char('n') | Char('N') => {
                if let Some(req) = self.pending.get(self.selection) {
                    self.pending_action = Some(ApprovalAction {
                        approval_id: req.id.clone(),
                        decision: "reject".to_string(),
                    });
                }
                true
            }
            Char('s') | Char('S') => {
                // Skip: advance selection without acting
                if self.selection + 1 < self.pending.len() {
                    self.selection += 1;
                }
                true
            }
            _ => false,
        }
    }

    /// Take and clear the pending action (called by app.rs after dispatching).
    pub fn take_action(&mut self) -> Option<ApprovalAction> {
        self.pending_action.take()
    }

    pub fn selected(&self) -> Option<&ApprovalRequest> {
        self.pending.get(self.selection)
    }
}

pub struct ApprovalQueue<'a> {
    pub state: &'a ApprovalQueueState,
}

impl<'a> Widget for ApprovalQueue<'a> {
    fn render(self, area: Rect, buf: &mut Buffer) {
        let bg = Block::default().style(theme::normal());
        bg.render(area, buf);

        let chunks = Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
            .split(area);

        render_queue(buf, chunks[0], self.state);
        render_detail(buf, chunks[1], self.state);
    }
}

fn render_queue(buf: &mut Buffer, area: Rect, state: &ApprovalQueueState) {
    let focused = state.focus == Focus::Queue;
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Fill(1), Constraint::Length(8)])
        .split(area);

    // Pending list
    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(theme::border_for(focused))
        .title(Span::styled(" Pending Approvals ", theme::panel_title()))
        .style(theme::normal());
    let inner = block.inner(outer[0]);
    block.render(outer[0], buf);

    if state.loading {
        Paragraph::new(Span::styled("loading…", theme::dim()))
            .style(theme::normal())
            .render(inner, buf);
        return;
    }

    if state.pending.is_empty() {
        Paragraph::new(Line::from(vec![
            Span::styled("✓ ", theme::ok()),
            Span::styled("no pending approvals", theme::dim()),
        ]))
        .style(theme::normal())
        .render(inner, buf);
    } else {
        let items: Vec<ListItem> = state.pending.iter().enumerate().map(|(i, req)| {
            let selected = i == state.selection && focused;
            let style = if selected { theme::selected() } else { theme::normal() };
            ListItem::new(Line::from(vec![
                Span::styled(risk_dot(&req.risk_level), risk_style(&req.risk_level)),
                Span::styled(format!(" {:<20} ", truncate(&req.tool, 18)), style),
                Span::styled(
                    req.mission_name.as_deref().unwrap_or("—"),
                    theme::dim(),
                ),
            ]))
        }).collect();
        let mut ls = ListState::default().with_selected(
            if focused { Some(state.selection) } else { None }
        );
        ratatui::widgets::StatefulWidget::render(
            List::new(items).style(theme::normal()),
            inner, buf, &mut ls,
        );
    }

    // History
    let hblock = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(theme::border_normal())
        .title(Span::styled(" Recent Decisions ", theme::panel_title()))
        .style(theme::normal());
    let hinner = hblock.inner(outer[1]);
    hblock.render(outer[1], buf);

    let hist_items: Vec<Line> = state.history.iter().rev().take(5).map(|(req, decision)| {
        let (dot, sty) = match decision.as_str() {
            "approved" => ("✓", theme::ok()),
            "denied" => ("✗", theme::err()),
            _ => ("?", theme::dim()),
        };
        Line::from(vec![
            Span::styled(dot, sty),
            Span::styled(format!(" {}", truncate(&req.tool, 16)), theme::dim()),
            Span::styled(format!("  {}", decision), sty),
        ])
    }).collect();
    Paragraph::new(hist_items).style(theme::normal()).render(hinner, buf);
}

fn render_detail(buf: &mut Buffer, area: Rect, state: &ApprovalQueueState) {
    let focused = state.focus == Focus::Detail;
    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(theme::border_for(focused))
        .title(Span::styled(" Request Detail ", theme::panel_title()))
        .style(theme::normal());
    let inner = block.inner(area);
    block.render(area, buf);

    let Some(req) = state.selected() else {
        Paragraph::new(Span::styled("select a request", theme::muted()))
            .style(theme::normal())
            .render(inner, buf);
        return;
    };

    let mut lines: Vec<Line> = vec![
        Line::from(vec![
            Span::styled("Tool   ", theme::muted()),
            Span::styled(req.tool.clone(), theme::accent()),
        ]),
        Line::from(vec![
            Span::styled("Risk   ", theme::muted()),
            Span::styled(risk_dot(&req.risk_level), risk_style(&req.risk_level)),
            Span::styled(format!(" {}", req.risk_level), risk_style(&req.risk_level)),
        ]),
        Line::from(vec![
            Span::styled("Agent  ", theme::muted()),
            Span::styled(req.agent_id.as_deref().unwrap_or("—"), theme::dim()),
        ]),
        Line::from(vec![
            Span::styled("Mission", theme::muted()),
            Span::styled(req.mission_name.as_deref().unwrap_or("—"), theme::dim()),
        ]),
        Line::from(""),
    ];

    if let Some(reasoning) = &req.reasoning {
        lines.push(Line::from(Span::styled("Reasoning", theme::muted())));
        for part in reasoning.lines().take(5) {
            lines.push(Line::from(Span::styled(part.to_string(), theme::dim())));
        }
        lines.push(Line::from(""));
    }

    if let Some(input) = &req.input_json {
        lines.push(Line::from(Span::styled("Input", theme::muted())));
        for part in input.lines().take(8) {
            lines.push(Line::from(Span::styled(
                part.to_string(),
                Style::default().fg(theme::PURPLE),
            )));
        }
        lines.push(Line::from(""));
    }

    // Action hint
    lines.push(Line::from(vec![
        Span::styled("  y ", theme::ok()),
        Span::styled("approve  ", theme::dim()),
        Span::styled("  n ", theme::err()),
        Span::styled("deny  ", theme::dim()),
        Span::styled("  s ", theme::muted()),
        Span::styled("skip", theme::muted()),
    ]));

    Paragraph::new(lines)
        .wrap(ratatui::widgets::Wrap { trim: true })
        .style(theme::normal())
        .render(inner, buf);
}

fn risk_dot(risk: &str) -> &'static str {
    match risk.to_lowercase().as_str() {
        "high" | "critical" => "●",
        "medium" => "●",
        _ => "●",
    }
}

fn risk_style(risk: &str) -> ratatui::style::Style {
    match risk.to_lowercase().as_str() {
        "high" | "critical" => theme::err(),
        "medium" => theme::warn(),
        _ => theme::ok(),
    }
}

fn truncate(s: &str, max: usize) -> String {
    if s.len() <= max { s.to_string() }
    else { format!("{}…", &s[..max.saturating_sub(1)]) }
}
