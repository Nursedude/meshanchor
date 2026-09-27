"""RNS event handlers for RNSMeshtasticBridge — LXMF receive + announce.

Extracted from rns_bridge.py (MF025 1,500-line cap) along the seam MeshForge
already runs (its gateway/bridge_rns_events_mixin.py). Bodies moved VERBATIM
except for the 2026-09-26 single-capture fix in _on_rns_announce; module
names are bound from gateway.rns_bridge at call time so the existing
patch("gateway.rns_bridge.<name>") test seams still apply.
"""


class BridgeRnsEventsMixin:
    """Mixin: _on_lxmf_receive / _on_rns_announce for RNSMeshtasticBridge."""

    def _on_lxmf_receive(self, message):
        """Handle incoming LXMF message"""
        # Module names resolve off the hub at call time so patch("gateway.rns_bridge.X")
        # keeps working for this extracted handler (MeshForge's shape).
        from . import rns_bridge as _h
        (BridgedMessage, Full, HAS_RNS_SNIFFER, RNSPacketInfo, RNSPacketType, UnifiedNode, get_rns_sniffer, logger) = (
            getattr(_h, "BridgedMessage", None), getattr(_h, "Full", None), getattr(_h, "HAS_RNS_SNIFFER", None), getattr(_h, "RNSPacketInfo", None), getattr(_h, "RNSPacketType", None), getattr(_h, "UnifiedNode", None), getattr(_h, "get_rns_sniffer", None), getattr(_h, "logger", None))
        try:
            # Update node info
            source_hash = message.source_hash
            node = UnifiedNode.from_rns(source_hash)
            self.node_tracker.add_node(node)

            # Capture LXMF message for traffic inspection
            if HAS_RNS_SNIFFER:
                try:
                    sniffer = get_rns_sniffer()
                    if sniffer and sniffer._running:
                        # LXMessage.content can be either bytes (binary LXMF
                        # payload) or str — encode only when we got text.
                        # (MeshForge #1162: 'bytes'.encode() raised, dropping
                        # the capture; delivery itself was unaffected.)
                        raw_content = message.content or b''
                        content_bytes = (
                            raw_content.encode('utf-8')
                            if isinstance(raw_content, str)
                            else raw_content
                        )
                        packet_info = RNSPacketInfo(
                            packet_type=RNSPacketType.DATA,
                            source_hash=source_hash,
                            direction="inbound",
                            payload=content_bytes,
                            payload_size=len(content_bytes),
                            announce_aspect="lxmf.delivery",
                        )
                        sniffer._store_packet(packet_info)
                except Exception as e:
                    logger.debug(f"RNS sniffer LXMF capture error: {e}")

            # Decode LXMF bytes->str up front so stored records and the
            # BridgedMessage carry clean text, not Python bytes reprs.
            # Without this, messages.db logs "[b'Meshtastic'] b'...'".
            content_str = (
                message.content.decode("utf-8", errors="replace")
                if isinstance(message.content, (bytes, bytearray))
                else (message.content or "")
            )
            title_str = (
                message.title.decode("utf-8", errors="replace")
                if isinstance(message.title, (bytes, bytearray))
                else (message.title or "")
            )

            msg = BridgedMessage(
                source_network="rns",
                source_id=source_hash.hex(),
                destination_id=None,
                content=content_str,
                title=title_str,
                metadata={
                    'lxmf_stamp': message.stamp,
                }
            )

            # Store incoming message for UI/history
            try:
                from commands import messaging
                # Combine title and content for RNS messages (decoded)
                content = content_str
                if title_str:
                    content = f"[{title_str}] {content}"
                messaging.store_incoming(
                    from_id=source_hash.hex(),
                    content=content,
                    network="rns",
                    to_id=None,  # LXMF doesn't have destination in received messages
                )
            except Exception as e:
                logger.debug(f"Could not store incoming RNS message: {e}")

            # Queue for bridging if enabled (non-blocking to prevent deadlock)
            if self._router.should_bridge(msg):
                try:
                    self._rns_to_mesh_queue.put_nowait(msg)
                except Full:
                    logger.warning("RNS→Mesh queue full, dropping message")
                    with self._stats_lock:
                        self.stats['errors'] += 1

            # Notify callbacks
            self._notify_message(msg)

            # LXMF→MeshCore re-emit hook. Only acts when the operator
            # opted in via meshtastic_reemit.enabled=True AND the LXMF
            # source_hash matches one of the configured
            # MeshtasticBroadcastBridge identities. Cheap no-op
            # otherwise. Wrapped so a bridge bug can't take down the
            # gateway's LXMF RX path.
            if self._meshtastic_reemit:
                try:
                    self._meshtastic_reemit.on_lxmf_message(
                        source_hash, content_str,
                    )
                except Exception as e:
                    logger.debug(f"meshtastic_reemit hook error: {e}")

        except Exception as e:
            logger.error(f"Error processing LXMF message: {e}")

    def _on_rns_announce(self, dest_hash, announced_identity, app_data):
        """Handle RNS announce for node discovery"""
        # Module names resolve off the hub at call time so patch("gateway.rns_bridge.X")
        # keeps working for this extracted handler (MeshForge's shape).
        from . import rns_bridge as _h
        (BridgedMessage, Full, HAS_RNS_SNIFFER, RNSPacketInfo, RNSPacketType, UnifiedNode, get_rns_sniffer, logger) = (
            getattr(_h, "BridgedMessage", None), getattr(_h, "Full", None), getattr(_h, "HAS_RNS_SNIFFER", None), getattr(_h, "RNSPacketInfo", None), getattr(_h, "RNSPacketType", None), getattr(_h, "UnifiedNode", None), getattr(_h, "get_rns_sniffer", None), getattr(_h, "logger", None))
        try:
            # Capture announce packet for traffic inspection
            if HAS_RNS_SNIFFER:
                try:
                    import RNS
                    sniffer = get_rns_sniffer()
                    # The sniffer registers its OWN announce handler (aspect
                    # filter None = every announce); capturing here too stored
                    # each announce TWICE (measured on meshanchor-server
                    # 2026-09-26: 180 same-second repeats in 400 rows). Capture
                    # here only as the fallback when the sniffer has no hooks
                    # (port of MeshForge 0f0502f4).
                    if (sniffer and sniffer._running
                            and not getattr(sniffer, "_hooks_installed", False)):
                        packet_info = RNSPacketInfo(
                            packet_type=RNSPacketType.ANNOUNCE,
                            destination_hash=dest_hash,
                            direction="inbound",
                            announce_app_data=app_data,
                            announce_aspect="lxmf.delivery",
                        )
                        # Get identity hash if available
                        if announced_identity:
                            try:
                                packet_info.source_hash = announced_identity.hash
                                packet_info.announce_identity = announced_identity.hash
                            except Exception:
                                pass
                        # Get hop count
                        try:
                            if RNS.Transport.has_path(dest_hash):
                                hops = RNS.Transport.hops_to(dest_hash)
                                packet_info.hops = hops if hops is not None else 0
                        except Exception:
                            pass
                        sniffer._store_packet(packet_info)
                except Exception as e:
                    logger.debug(f"RNS sniffer capture error: {e}")

            node = UnifiedNode.from_rns(dest_hash, app_data=app_data)
            self.node_tracker.add_node(node)
            logger.debug(f"Discovered RNS node: {dest_hash.hex()[:8]}")
        except Exception as e:
            logger.error(f"Error processing RNS announce: {e}")

    # Routing delegated to MessageRouter (see gateway/message_routing.py)
