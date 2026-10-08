#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "ladderbook/book.hpp"

#include "ladderbook/fees.hpp"

namespace py = pybind11;
using namespace ladderbook;

PYBIND11_MODULE(_core, m) {
    m.doc() = "ladderbook C++ core: fixed-point fees, order book and replay engine";
    m.attr("PRICE_ONE") = kPriceOne;
    m.attr("QTY_ONE") = kQtyOne;
    m.attr("TAKER_RATE") = kTakerRate;
    m.attr("MAKER_RATE") = kMakerRate;

    m.def("trade_fee_micros", &trade_fee_micros, py::arg("rate"), py::arg("multiplier"), py::arg("price"),
          py::arg("qty"), "Kalshi trade fee in micro-dollars (exact integer arithmetic).");

    py::class_<FillCash>(m, "FillCash")
        .def_readonly("balance_change", &FillCash::balance_change)
        .def_readonly("trade_fee", &FillCash::trade_fee)
        .def_readonly("rounding_fee", &FillCash::rounding_fee)
        .def_readonly("rebate", &FillCash::rebate);

    py::class_<OrderFeeAccumulator>(m, "OrderFeeAccumulator")
        .def(py::init<Micros>(), py::arg("precision") = 10'000)
        .def("apply", &OrderFeeAccumulator::apply, py::arg("revenue"), py::arg("trade_fee"))
        .def_property_readonly("carried", &OrderFeeAccumulator::carried);

    py::enum_<Side>(m, "Side").value("BID", Side::Bid).value("ASK", Side::Ask);

    py::class_<BinaryBook>(m, "BinaryBook")
        .def(py::init<>())
        .def("clear", &BinaryBook::clear)
        .def("set_level", &BinaryBook::set_level, py::arg("side"), py::arg("price"), py::arg("qty"))
        .def("apply_delta", &BinaryBook::apply_delta, py::arg("side"), py::arg("price"), py::arg("delta"))
        .def("best_bid", &BinaryBook::best_bid)
        .def("best_ask", &BinaryBook::best_ask)
        .def("depth_at", &BinaryBook::depth_at, py::arg("side"), py::arg("price"))
        .def("levels", &BinaryBook::levels, py::arg("side"))
        .def("crossed", &BinaryBook::crossed);
}
