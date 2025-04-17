use std::collections::HashMap;
use std::hash::Hash;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use num_cpus;
use rayon::{prelude::*, ThreadPoolBuilder};

// MAIN

fn main() {
    // Number of transactions to benchmark
    let num_txs = 10_000;
    // Prepare a batch of simple AddTx transactions
    let mut txs: Vec<Box<dyn Transaction<usize, i32>>> = Vec::with_capacity(num_txs);
    for i in 0..num_txs {
        txs.push(Box::new(AddTx { key: i % 100, delta: 1 }));
    }

    // Create shared state
    let state = State::<usize, i32>::new();

    // Benchmark execution
    let start = Instant::now();
    let result = BlockSTM::execute_block(&txs, &state);
    let duration = start.elapsed();

    println!("Executed {} transactions in {:?}", num_txs, duration);
    println!("Final value for key 0: {:?}", state.get(&0));
    println!("Result map contains {}", result.len());
}

// Simple transaction that increments a key by a fixed delta
struct AddTx {
    key: usize,
    delta: i32,
}

impl Transaction<usize, i32> for AddTx {
    fn execute(&self, state: &State<usize, i32>) -> (HashMap<usize, Version>, HashMap<usize, i32>) {
        let mut read = HashMap::new();
        let mut write = HashMap::new();
        let (cur, ver) = state.get(&self.key).unwrap_or((0, 0));
        read.insert(self.key, ver);
        write.insert(self.key, cur + self.delta);
        (read, write)
    }
}


// MAIN

// A version counter for optimistic concurrency validation
pub type Version = u64;

/// A value tagged with a version for OCC
#[derive(Clone)]
pub struct VersionedValue<V> {
    pub version: Version,
    pub value: V,
}

/// Global shared state, stores versioned values behind a mutex
#[derive(Clone)]
pub struct State<K, V>
where
    K: Eq + Hash + Clone,
    V: Clone,
{
    inner: Arc<Mutex<HashMap<K, VersionedValue<V>>>>,
}

impl<K, V> State<K, V>
where
    K: Eq + Hash + Clone,
    V: Clone,
{
    /// Create an empty state
    pub fn new() -> Self {
        State {
            inner: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Read a key, returning (value, version) if present
    pub fn get(&self, key: &K) -> Option<(V, Version)> {
        let map = self.inner.lock().unwrap();
        map.get(key).map(|vv| (vv.value.clone(), vv.version))
    }

    /// Apply a write set by bumping versions and storing new values
    pub fn apply_write_set(&self, write_set: &HashMap<K, V>) {
        let mut map = self.inner.lock().unwrap();
        for (key, val) in write_set.iter() {
            let next_version = map.get(key).map(|vv| vv.version + 1).unwrap_or(1);
            map.insert(
                key.clone(),
                VersionedValue { version: next_version, value: val.clone() },
            );
        }
    }
}

/// A transaction that can be executed against the global state
pub trait Transaction<K, V>: Send + Sync
where
    K: Eq + Hash + Clone,
    V: Clone,
{
    /// Execute the transaction, reading directly from state,
    /// producing a read set of (key -> version) and a write set of (key -> new value).
    fn execute(&self, state: &State<K, V>) -> (HashMap<K, Version>, HashMap<K, V>);
}

/// Block-level STM: executes a block of transactions with speculative parallelism
pub struct BlockSTM;

impl BlockSTM {
    /// Execute a block of heterogeneous transactions in serial order semantics.
    /// Accepts a slice of boxed trait objects.
    pub fn execute_block<K, V>(
        block: &Vec<Box<dyn Transaction<K, V>>>,
        global_state: &State<K, V>,
    ) -> HashMap<K, V>
    where
        K: Eq + Hash + Clone + Send + Sync,
        V: Clone + Send,
    {
        // Build a local Rayon pool with all logical cores
        let pool = ThreadPoolBuilder::new()
            .num_threads(num_cpus::get())
            .build()
            .unwrap();

        // Phase 1: speculative execute all tx in parallel against initial state
        let mut speculative: Vec<(usize, HashMap<K, Version>, HashMap<K, V>)> = pool.install(|| {
            block.par_iter()
                .enumerate()
                .map(|(idx, tx)| {
                    let (rs, ws) = tx.execute(global_state);
                    (idx, rs, ws)
                })
                .collect()
        });
        speculative.sort_by_key(|(idx, _, _)| *idx);

        // Phase 2: sequential commit in original order, re-executing if reads are stale
        let mut final_write_set: HashMap<K, V> = HashMap::new();
        for (idx, rs_spec, ws_spec) in speculative {
            let mut read_set = rs_spec;
            let mut write_set = ws_spec;
            // Retry if speculative read-set is no longer valid
            while !read_set.iter().all(|(k, &v)| {
                global_state.get(k)
                    .map(|(_, cur)| cur == v)
                    .unwrap_or(true)
            }) {
                let (rs, ws) = block[idx].execute(global_state);
                read_set = rs;
                write_set = ws;
            }
            // Commit this tx
            global_state.apply_write_set(&write_set);
            for (k, v) in write_set {
                final_write_set.insert(k, v);
            }
        }

        final_write_set
    }
}

// ---------------------- Tests ----------------------
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// A simple transaction that sets a key to a value, no prior read.
    struct SetTx {
        key: String,
        value: i32,
    }
    impl Transaction<String, i32> for SetTx {
        fn execute(&self, _state: &State<String, i32>) -> (HashMap<String, Version>, HashMap<String, i32>) {
            (HashMap::new(), vec![(self.key.clone(), self.value)].into_iter().collect())
        }
    }

    /// A transaction that increments a key by a fixed amount.
    struct AddTx {
        key: String,
        delta: i32,
    }
    impl Transaction<String, i32> for AddTx {
        fn execute(&self, state: &State<String, i32>) -> (HashMap<String, Version>, HashMap<String, i32>) {
            let mut read = HashMap::new();
            let mut write = HashMap::new();
            let (cur, ver) = state.get(&self.key).unwrap_or((0, 0));
            read.insert(self.key.clone(), ver);
            write.insert(self.key.clone(), cur + self.delta);
            (read, write)
        }
    }

    #[test]
    fn test_non_conflicting() {
        let state = State::<String, i32>::new();
        let txs: Vec<Box<dyn Transaction<String, i32>>> = vec![
            Box::new(SetTx { key: "a".into(), value: 1 }),
            Box::new(SetTx { key: "b".into(), value: 2 }),
        ];
        let result = BlockSTM::execute_block(&txs, &state);
        assert_eq!(state.get(&"a".into()), Some((1, 1)));
        assert_eq!(state.get(&"b".into()), Some((2, 1)));
        assert_eq!(result.get("a"), Some(&1));
        assert_eq!(result.get("b"), Some(&2));
    }

    #[test]
    fn test_conflicting_add() {
        let state = State::<String, i32>::new();
        let txs: Vec<Box<dyn Transaction<String, i32>>> = vec![
            Box::new(AddTx { key: "x".into(), delta: 5 }),
            Box::new(AddTx { key: "x".into(), delta: 3 }),
        ];
        let result = BlockSTM::execute_block(&txs, &state);
        assert_eq!(state.get(&"x".into()), Some((8, 2)));
        assert_eq!(result.get("x"), Some(&8));
    }

    #[test]
    fn test_order_semantics() {
        let state = State::<String, i32>::new();
        let tx1: Box<dyn Transaction<String, i32>> = Box::new(SetTx { key: "a".into(), value: 10 });
        let tx2: Box<dyn Transaction<String, i32>> = Box::new(AddTx { key: "a".into(), delta: 7 });
        let txs = vec![tx1, tx2];
        let result = BlockSTM::execute_block(&txs, &state);
        assert_eq!(state.get(&"a".into()), Some((17, 2)));
        assert_eq!(result.get("a"), Some(&17));
    }

    #[test]
    fn test_parallel_scaling() {
        let state = State::<String, i32>::new();
        let mut txs: Vec<Box<dyn Transaction<String, i32>>> = Vec::new();
        for i in 0..100 {
            txs.push(Box::new(SetTx { key: i.to_string(), value: i }));
        }
        let result = BlockSTM::execute_block(&txs, &state);
        for i in 0..100 {
            assert_eq!(state.get(&i.to_string()), Some((i, 1)));
            assert_eq!(result.get(&i.to_string()), Some(&i));
        }
    }

    #[test]
    fn test_reexecution_metrics() {
        static COUNTER: AtomicUsize = AtomicUsize::new(0);
        struct CountTx;
        impl Transaction<String, i32> for CountTx {
            fn execute(&self, _state: &State<String, i32>) -> (HashMap<String, Version>, HashMap<String, i32>) {
                COUNTER.fetch_add(1, Ordering::SeqCst);
                (HashMap::new(), HashMap::new())
            }
        }
        let state = State::<String, i32>::new();
        let txs: Vec<Box<dyn Transaction<String, i32>>> = vec![
            Box::new(CountTx), Box::new(CountTx), Box::new(CountTx),
        ];
        let _ = BlockSTM::execute_block(&txs, &state);
        assert!(COUNTER.load(Ordering::SeqCst) >= 3);
    }
}
