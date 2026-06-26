export interface DetectionBox {
  cls: string;
  bbox: [number, number, number, number]; // [x, y, w, h] in frame pixels
  track_id: number;
  no_hardhat: boolean;
}

export interface DetectionFrame {
  camera_id: string;
  ts: number;
  frame_w: number;
  frame_h: number;
  seq: number;
  boxes: DetectionBox[];
}
