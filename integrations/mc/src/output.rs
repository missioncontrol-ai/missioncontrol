use clap::ValueEnum;
use serde_json::Value;

#[derive(Copy, Clone, Debug, Eq, PartialEq, ValueEnum)]
pub enum OutputMode {
    Human,
    Json,
    Jsonl,
}

impl OutputMode {
    pub fn is_machine(self) -> bool {
        matches!(self, Self::Json | Self::Jsonl)
    }
}

pub fn print_value(mode: OutputMode, value: &Value) {
    match mode {
        OutputMode::Human | OutputMode::Json => {
            println!(
                "{}",
                serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
            );
        }
        OutputMode::Jsonl => {
            println!(
                "{}",
                serde_json::to_string(value).unwrap_or_else(|_| value.to_string())
            );
        }
    }
}
