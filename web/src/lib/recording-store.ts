import "client-only";

export type LocalRecording = {
  id: string;
  sessionId: string;
  mimeType: string;
  filename: string;
  startOffset: number;
  sessionDuration: number;
  startedAt: number;
  updatedAt: number;
  durationSeconds: number;
  nextSequence: number;
  nextStreamIndex: number;
  status: "recording" | "interrupted" | "saving" | "save_failed";
};

export type LocalRecordingChunk = {
  recordingId: string;
  sequence: number;
  blob: Blob;
  mimeType: string;
  streamId: string;
  streamIndex: number;
  checksum: string;
  uploaded: boolean;
  createdAt: number;
};

const DATABASE_NAME = "knowledgedebt-recordings";
const DATABASE_VERSION = 1;
const RECORDINGS = "recordings";
const CHUNKS = "chunks";

let databasePromise: Promise<IDBDatabase> | null = null;

function database(): Promise<IDBDatabase> {
  if (databasePromise) return databasePromise;
  databasePromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(RECORDINGS)) {
        db.createObjectStore(RECORDINGS, { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains(CHUNKS)) {
        const chunks = db.createObjectStore(CHUNKS, { keyPath: ["recordingId", "sequence"] });
        chunks.createIndex("by_recording", "recordingId");
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("无法打开录音恢复存储"));
    request.onblocked = () => reject(new Error("录音恢复存储正在被另一个页面升级，请关闭旧页面后重试"));
  });
  return databasePromise;
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("录音本地存储操作失败"));
  });
}

function transactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error ?? new Error("录音本地存储事务失败"));
    transaction.onabort = () => reject(transaction.error ?? new Error("录音本地存储事务已中止"));
  });
}

export async function saveLocalRecording(recording: LocalRecording): Promise<void> {
  const db = await database();
  const transaction = db.transaction(RECORDINGS, "readwrite", { durability: "strict" });
  transaction.objectStore(RECORDINGS).put(recording);
  await transactionDone(transaction);
}

export async function getLocalRecording(recordingId: string): Promise<LocalRecording | undefined> {
  const db = await database();
  const transaction = db.transaction(RECORDINGS, "readonly");
  const value = await requestResult(
    transaction.objectStore(RECORDINGS).get(recordingId) as IDBRequest<LocalRecording | undefined>,
  );
  await transactionDone(transaction);
  return value ? { ...value, nextStreamIndex: value.nextStreamIndex ?? 0 } : undefined;
}

export async function listLocalRecordings(sessionId: string): Promise<LocalRecording[]> {
  const db = await database();
  const transaction = db.transaction(RECORDINGS, "readonly");
  const values = await requestResult(
    transaction.objectStore(RECORDINGS).getAll() as IDBRequest<LocalRecording[]>,
  );
  await transactionDone(transaction);
  return values
    .filter((recording) => recording.sessionId === sessionId)
    .map((recording) => ({ ...recording, nextStreamIndex: recording.nextStreamIndex ?? 0 }))
    .sort((left, right) => right.updatedAt - left.updatedAt);
}

export async function saveLocalChunk(chunk: LocalRecordingChunk): Promise<void> {
  const db = await database();
  const transaction = db.transaction(CHUNKS, "readwrite", { durability: "strict" });
  transaction.objectStore(CHUNKS).put(chunk);
  await transactionDone(transaction);
}

export async function markLocalChunkUploaded(
  recordingId: string,
  sequence: number,
): Promise<void> {
  const db = await database();
  const transaction = db.transaction(CHUNKS, "readwrite", { durability: "strict" });
  const store = transaction.objectStore(CHUNKS);
  const chunk = await requestResult(
    store.get([recordingId, sequence]) as IDBRequest<LocalRecordingChunk | undefined>,
  );
  if (chunk) store.put({ ...chunk, uploaded: true });
  await transactionDone(transaction);
}

export async function listLocalChunks(recordingId: string): Promise<LocalRecordingChunk[]> {
  const db = await database();
  const transaction = db.transaction(CHUNKS, "readonly");
  const index = transaction.objectStore(CHUNKS).index("by_recording");
  const chunks = await requestResult(
    index.getAll(IDBKeyRange.only(recordingId)) as IDBRequest<LocalRecordingChunk[]>,
  );
  await transactionDone(transaction);
  return chunks
    .map((chunk) => ({
      ...chunk,
      streamId: chunk.streamId ?? "legacy",
      streamIndex: chunk.streamIndex ?? 0,
    }))
    .sort((left, right) => left.sequence - right.sequence);
}

export async function removeLocalRecording(recordingId: string): Promise<void> {
  const db = await database();
  const transaction = db.transaction([RECORDINGS, CHUNKS], "readwrite", { durability: "strict" });
  transaction.objectStore(RECORDINGS).delete(recordingId);
  const chunks = transaction.objectStore(CHUNKS).index("by_recording");
  const keys = await requestResult(chunks.getAllKeys(IDBKeyRange.only(recordingId)));
  const chunkStore = transaction.objectStore(CHUNKS);
  for (const key of keys) chunkStore.delete(key);
  await transactionDone(transaction);
}

export async function sha256(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}
