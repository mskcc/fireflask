$(document).ready(function() {
    $("#dbmenu a").click(function(e) {
        var base = location.protocol + '//' + location.host;
        window.location.href=base + "/" +  $(this).attr('href').substring(1);
        return false;
    });
    $("#wf_delete").click(function(e) {
        $('input[type="checkbox"]:checked').each(function() {
            $.ajax({
                url: "/"+$("#dbname").text() + "/wf/" + $(this).val() + "/delete",
            });
        });
        $('input[type="checkbox"]:checked').promise().done(function() {
            location.reload();
        });
    });
    $("#wf_rerun").click(function(e) {
        $('input[type="checkbox"]:checked').each(function() {
            $.ajax({
                url: "/"+$("#dbname").text() + "/wf/" + $(this).val() + "/rerun",
            });
        });
        $('input[type="checkbox"]:checked').promise().done(function() {
            location.reload();
        });
    });

    $("#check_all").click(function(e) {
        $('input[type="checkbox"]').each(function() {
            $(this).prop("checked", true);
        });
    });
    $("#check_none").click(function(e) {
        $('input[type="checkbox"]').each(function() {
            $(this).prop("checked", false);
        });
    });
    if ($.inArray($("#fw_state").html() , ["COMPLETED", "RESERVED", "ARCHIVED", "RUNNING"]) > -1) {
        $("#bsub_options_form input").prop('disabled', true);
        $("#modify_bsub").prop('disabled', true).html("can't modify " + $("#fw_state").html() + " job options");
    }

});


