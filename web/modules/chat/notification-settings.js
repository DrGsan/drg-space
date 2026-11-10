export const roomMuted=room=>room.mute_until===-1||Number(room.mute_until)>Date.now()/1000;
