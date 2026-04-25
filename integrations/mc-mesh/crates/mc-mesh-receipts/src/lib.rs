pub mod error;
pub mod store;
pub mod types;

pub use error::ReceiptsError;
pub use store::ReceiptStore;
pub use types::{Receipt, ReceiptFilter};

/// Default path: `~/.missioncontrol/receipts.db`
pub fn default_db_path() -> std::path::PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| std::path::PathBuf::from("."))
        .join(".missioncontrol")
        .join("receipts.db")
}
