use ratatui::{
    buffer::Buffer,
    layout::{Alignment, Constraint, Direction, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Span},
    widgets::{Block, BorderType, Borders, Paragraph, Widget},
};

use crate::theme;

pub struct LandingScreen;

impl Widget for LandingScreen {
    fn render(self, area: Rect, buf: &mut Buffer) {
        // Fill background
        let block = Block::default().style(theme::normal());
        block.render(area, buf);

        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Fill(1), Constraint::Length(6), Constraint::Fill(1)])
            .split(area);

        let nav_keys = vec![
            Line::from(vec![
                Span::styled("  m  ", theme::accent_bold()),
                Span::styled("mission matrix   ", theme::dim()),
                Span::styled("  a  ", theme::accent_bold()),
                Span::styled("approvals        ", theme::dim()),
            ]),
            Line::from(vec![
                Span::styled("  f  ", theme::accent_bold()),
                Span::styled("agent feed       ", theme::dim()),
                Span::styled("  q  ", theme::accent_bold()),
                Span::styled("receipts         ", theme::dim()),
            ]),
            Line::from(""),
            Line::from(vec![
                Span::styled("  s  ", theme::muted()),
                Span::styled("secrets browser  ", theme::muted()),
                Span::styled("  ?  ", theme::muted()),
                Span::styled("help             ", theme::muted()),
            ]),
            Line::from(vec![
                Span::styled("  Ctrl+Q  ", theme::muted()),
                Span::styled("quit", theme::muted()),
            ]),
        ];

        let para = Paragraph::new(nav_keys)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_type(BorderType::Rounded)
                    .border_style(theme::border_normal())
                    .title(Span::styled(
                        " MissionControl ",
                        Style::default()
                            .fg(theme::ACCENT)
                            .add_modifier(Modifier::BOLD),
                    ))
                    .title_alignment(Alignment::Center)
                    .style(theme::normal()),
            )
            .alignment(Alignment::Center);

        para.render(chunks[1], buf);
    }
}
