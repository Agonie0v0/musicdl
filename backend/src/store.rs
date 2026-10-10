use anyhow::Result;
use rusqlite::{Connection, params};
use serde::{Serialize, de::DeserializeOwned};
use std::{
    path::Path,
    sync::{Arc, Mutex},
};

#[derive(Clone)]
pub struct Store(Arc<Mutex<Connection>>);
impl Store {
    pub fn open(path: &Path) -> Result<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let db = Connection::open(path)?;
        db.execute_batch("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=3000; CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, updated INTEGER NOT NULL DEFAULT (unixepoch()), PRIMARY KEY(kind,id));")?;
        Ok(Self(Arc::new(Mutex::new(db))))
    }
    async fn run<T: Send + 'static>(
        &self,
        f: impl FnOnce(&mut Connection) -> Result<T> + Send + 'static,
    ) -> Result<T> {
        let db = self.0.clone();
        tokio::task::spawn_blocking(move || {
            let mut guard = db.lock().map_err(|_| anyhow::anyhow!("数据库锁失效"))?;
            f(&mut guard)
        })
        .await?
    }
    pub async fn put<T: Serialize>(&self, kind: &str, id: &str, value: &T) -> Result<()> {
        let (kind, id, body) = (
            kind.to_owned(),
            id.to_owned(),
            serde_json::to_string(value)?,
        );
        self.run(move |db| { db.execute("INSERT INTO records(kind,id,body) VALUES (?1,?2,?3) ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body, updated=unixepoch()",params![kind,id,body])?;Ok(()) }).await
    }
    pub async fn get<T: DeserializeOwned + Send + 'static>(
        &self,
        kind: &str,
        id: &str,
    ) -> Result<Option<T>> {
        let (kind, id) = (kind.to_owned(), id.to_owned());
        self.run(move |db| {
            let mut stmt = db.prepare("SELECT body FROM records WHERE kind=?1 AND id=?2")?;
            let mut rows = stmt.query(params![kind, id])?;
            match rows.next()? {
                Some(row) => Ok(Some(serde_json::from_str(&row.get::<_, String>(0)?)?)),
                None => Ok(None),
            }
        })
        .await
    }
    pub async fn list<T: DeserializeOwned + Send + 'static>(
        &self,
        kind: &str,
        limit: usize,
    ) -> Result<Vec<T>> {
        let kind = kind.to_owned();
        self.run(move |db| {
            let mut stmt = db.prepare(
                "SELECT body FROM records WHERE kind=?1 ORDER BY updated DESC,id DESC LIMIT ?2",
            )?;
            let rows = stmt.query_map(params![kind, limit as i64], |r| r.get::<_, String>(0))?;
            rows.map(|r| Ok(serde_json::from_str(&r?)?)).collect()
        })
        .await
    }
    pub async fn delete(&self, kind: &str, id: &str) -> Result<()> {
        let (kind, id) = (kind.to_owned(), id.to_owned());
        self.run(move |db| {
            db.execute(
                "DELETE FROM records WHERE kind=?1 AND id=?2",
                params![kind, id],
            )?;
            Ok(())
        })
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn records_survive_reopen_and_update() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.db");
        let db = Store::open(&path).unwrap();
        db.put("favorite", "track1", &vec!["song"]).await.unwrap();
        db.put("favorite", "track1", &vec!["updated"])
            .await
            .unwrap();
        drop(db);
        let db = Store::open(&path).unwrap();
        assert_eq!(
            db.get::<Vec<String>>("favorite", "track1")
                .await
                .unwrap()
                .unwrap(),
            vec!["updated"]
        );
        db.delete("favorite", "track1").await.unwrap();
        assert!(
            db.get::<Vec<String>>("favorite", "track1")
                .await
                .unwrap()
                .is_none()
        );
    }
}
